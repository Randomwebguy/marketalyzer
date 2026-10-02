"""FastAPI app: the backtest, walk-forward and paper trading tools as a web app.

It serves an installable app (a PWA: phone home screen or desktop window) and
a JSON API. Every request except the static files needs the access token,
because the app is meant to be reachable through a public tunnel. The login
page stores it in a cookie.
"""

import os
import secrets
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.data import load_ohlcv
from marketalyzer.backtest.engine import BacktestReport, optimize_backtest, run_backtest
from marketalyzer.backtest.strategies import STRATEGIES, strategy_params
from marketalyzer.paper.account import OrderRejected, PaperAccount, normalize_symbol
from marketalyzer.paper.cli import account_path
from marketalyzer.paper.feed import ProviderFeed
from marketalyzer.paper.models import IST
from marketalyzer.paper.trader import PaperTrader
from marketalyzer.walkforward import walk_forward
from openbb_bist.utils.constants import BIST_INDICES

STATIC = Path(__file__).with_name("static")
PUBLIC = ("/static/", "/login", "/manifest.webmanifest", "/sw.js", "/favicon.ico")
COOKIE_DAYS = 30
MAX_POINTS = 400
# Range code -> (bar interval, calendar days to load).
RANGES = {
    "1G": ("5m", 5),
    "1H": ("1h", 9),
    "1A": ("1d", 31),
    "3A": ("1d", 92),
    "6A": ("1d", 183),
    "1Y": ("1d", 366),
    "5Y": ("1W", 1830),
}


class Costs(BaseModel):
    """Trading costs shared by every simulation request."""

    commission: float = Field(0.002, ge=0, lt=0.1)
    bsmv: float = Field(0.05, ge=0, lt=1)
    min_commission: float = Field(0.0, ge=0)
    slippage: float = Field(0.001, ge=0, lt=0.1)
    cash: float = Field(100_000, gt=0)

    def costs(self) -> BistCosts:
        """Return the BistCosts for these settings."""
        return BistCosts(self.commission, self.bsmv, self.min_commission)


class BacktestRequest(Costs):
    """A backtest, optionally with parameter optimization."""

    symbol: str
    strategy: str = "sma_cross"
    start: date | None = None
    end: date | None = None
    params: dict[str, float] = Field(default_factory=dict)
    optimize: bool = False
    holdout: float = Field(0.3, ge=0, lt=1)
    benchmark: bool = True


class WalkForwardRequest(Costs):
    """A walk-forward test."""

    symbol: str
    strategy: str = "sma_cross"
    start: date
    end: date | None = None
    train: int = Field(504, ge=20)
    test: int = Field(126, ge=1)


class AccountRequest(Costs):
    """Settings for a new paper account."""

    dividend_tax: float = Field(0.15, ge=0, lt=1)
    reset: bool = False


class OrderRequest(BaseModel):
    """A manual paper order."""

    symbol: str
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0)
    type: Literal["market", "limit", "stop"] = "market"
    price: float | None = None
    tif: Literal["day", "gtc"] = "day"


class StepRequest(BaseModel):
    """One live trading step, as ``marketalyzer-paper run --once``."""

    symbols: list[str] = Field(min_length=1)
    strategy: str | None = None
    params: dict[str, float] = Field(default_factory=dict)
    interval: Literal["1d", "1h", "30m", "15m", "5m", "1m"] = "1d"


def _whole(params: dict[str, float]) -> dict[str, Any]:
    """Turn 10.0 into 10, so integer strategy parameters stay integers."""
    return {k: int(v) if float(v).is_integer() else v for k, v in params.items()}


def _strategy(name: str) -> str:
    if name not in STRATEGIES:
        raise HTTPException(400, f"Bilinmeyen strateji: {name}")
    return name


def _thin(items: list[Any], limit: int = MAX_POINTS) -> list[Any]:
    """Keep at most ``limit`` evenly spaced items, always including the last."""
    if len(items) <= limit:
        return items
    step = len(items) / limit
    picked = [items[int(i * step)] for i in range(limit - 1)]
    return [*picked, items[-1]]


def _stamp(value: Any) -> str:
    moment = pd.Timestamp(value)
    if moment.hour == moment.minute == 0:
        return moment.date().isoformat()
    return moment.isoformat()


def _series(report: BacktestReport) -> dict[str, Any]:
    """Equity, buy-and-hold and trades of a backtest, for charting."""
    equity = report.equity_curve["Equity"]
    points = [{"t": _stamp(t), "v": round(float(v), 2)} for t, v in equity.items()]
    hold = []
    if report.data is not None:
        close = report.data["Close"].reindex(equity.index)
        start = float(equity.iloc[0])
        hold = [
            {"t": _stamp(t), "v": round(start * float(c) / float(close.iloc[0]), 2)}
            for t, c in close.items()
        ]
    trades = [
        {
            "entry_time": _stamp(row.EntryTime),
            "exit_time": _stamp(row.ExitTime),
            "size": int(row.Size),
            "entry_price": round(float(row.EntryPrice), 4),
            "exit_price": round(float(row.ExitPrice), 4),
            "pnl": round(float(row.PnL), 2),
            "return_pct": round(float(row.ReturnPct) * 100, 4),
        }
        for row in report.trades.itertuples()
    ]
    return {"equity": _thin(points), "hold": _thin(hold), "trade_list": trades}


def _quote(symbol: str) -> dict[str, Any]:
    """Last price, day change and a 30-day sparkline from daily bars."""
    today = datetime.now(IST).date()
    frame = load_ohlcv(
        symbol, today - timedelta(days=60), today, adjustment="splits_only", cache=False
    )
    close = frame["Close"]
    previous = (
        float(close.iloc[-2]) if len(close) > 1 else float(frame["Open"].iloc[-1])
    )
    last = float(close.iloc[-1])
    return {
        "symbol": symbol,
        "name": BIST_INDICES.get(symbol),
        "last": last,
        "change_pct": round((last / previous - 1) * 100, 2),
        "date": frame.index[-1].date().isoformat(),
        "spark": [round(float(v), 4) for v in close.iloc[-30:]],
    }


def _thread_pool(processes=None, initializer=None, initargs=()):
    """Return a thread pool with the multiprocessing.Pool interface."""
    from multiprocessing.dummy import Pool

    return Pool(processes, initializer, initargs)


def create_app(
    token: str | None = None, account_name: str = "web", demo: bool = False
) -> FastAPI:
    """Build the app. ``account_name`` names the paper account it manages."""
    import backtesting

    # Requests run in server threads, and backtesting.py optimizes in processes
    # it forks; forking a multi-threaded process can deadlock. Threads are slower
    # but safe here.
    backtesting.Pool = _thread_pool

    token = token or os.environ.get("MARKETALYZER_TOKEN") or secrets.token_urlsafe(16)
    app = FastAPI(title="marketalyzer", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    plots = Path(tempfile.mkdtemp(prefix="marketalyzer-plots-"))
    paper_path = account_path(account_name)

    def authorized(request: Request) -> bool:
        header = request.headers.get("authorization", "")
        supplied = (
            request.query_params.get("token")
            or request.cookies.get("token")
            or header.removeprefix("Bearer ").strip()
        )
        return secrets.compare_digest(supplied.encode(), token.encode())

    def remember(request: Request, response: Any) -> Any:
        secure = request.headers.get("x-forwarded-proto", request.url.scheme)
        response.set_cookie(
            "token",
            token,
            max_age=COOKIE_DAYS * 86_400,
            httponly=True,
            samesite="lax",
            secure=secure == "https",
        )
        return response

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        path = request.url.path
        if path.startswith(PUBLIC):
            return await call_next(request)
        if authorized(request):
            response = await call_next(request)
            if request.query_params.get("token"):
                remember(request, response)
            return response
        if path == "/":
            return FileResponse(STATIC / "login.html", status_code=401)
        return JSONResponse(
            {"detail": "Erişim anahtarı gerekli: önce giriş yapın."}, status_code=401
        )

    @app.exception_handler(OrderRejected)
    async def rejected(_: Request, error: OrderRejected):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.post("/login")
    async def login(request: Request):
        form = parse_qs((await request.body()).decode())
        supplied = (form.get("token") or [""])[0].strip()
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            return RedirectResponse("/?error=1", status_code=303)
        return remember(request, RedirectResponse("/", status_code=303))

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(
            STATIC / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(STATIC / "sw.js", media_type="text/javascript")

    @app.get("/favicon.ico")
    def favicon() -> FileResponse:
        return FileResponse(STATIC / "icons" / "icon-192.png", media_type="image/png")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "demo": demo,
            "account": account_name,
            "strategies": {
                name: {
                    "doc": (cls.__doc__ or "").strip(),
                    "params": strategy_params(cls),
                    "grid": getattr(cls, "param_grid", {}),
                }
                for name, cls in STRATEGIES.items()
            },
            "ranges": list(RANGES),
        }

    @app.get("/api/strategies")
    def strategies() -> dict[str, Any]:
        return meta()["strategies"]

    @app.get("/api/prices")
    def prices(symbol: str, span: str = "3A") -> dict[str, Any]:
        if span not in RANGES:
            raise HTTPException(400, f"Geçersiz aralık: {span}")
        interval, days = RANGES[span]
        code = normalize_symbol(symbol)
        today = datetime.now(IST).date()
        try:
            frame = load_ohlcv(
                code,
                today - timedelta(days=days),
                today,
                interval,
                "splits_only",
                cache=False,
            )
        except Exception as error:
            raise HTTPException(400, str(error)) from error
        if span == "1G":
            frame = frame[frame.index.date == frame.index[-1].date()]
        rows = [
            {
                "t": _stamp(t),
                "o": row.Open,
                "h": row.High,
                "l": row.Low,
                "c": row.Close,
                "v": row.Volume,
            }
            for t, row in frame.iterrows()
        ]
        first, last = rows[0]["o"], rows[-1]["c"]
        return {
            "symbol": code,
            "name": BIST_INDICES.get(code),
            "span": span,
            "interval": interval,
            "last": last,
            "change_pct": round((last / first - 1) * 100, 2),
            "rows": rows,
        }

    @app.get("/api/watchlist")
    def watchlist(symbols: str) -> list[dict[str, Any]]:
        codes = [normalize_symbol(s) for s in symbols.split(",") if s.strip()]
        codes = list(dict.fromkeys(codes))[:12]
        if not codes:
            return []

        def one(code: str) -> dict[str, Any]:
            try:
                return _quote(code)
            except Exception as error:
                return {"symbol": code, "error": str(error)}

        with ThreadPoolExecutor(max_workers=min(6, len(codes))) as pool:
            return list(pool.map(one, codes))

    @app.post("/api/backtest")
    def backtest(request: BacktestRequest) -> dict[str, Any]:
        common = {
            "start": request.start,
            "end": request.end,
            "cash": request.cash,
            "costs": request.costs(),
            "slippage": request.slippage,
            "benchmark": "XU100" if request.benchmark else None,
            "usd": request.benchmark,
        }
        strategy = _strategy(request.strategy)
        try:
            if request.optimize:
                result = optimize_backtest(
                    request.symbol, strategy, holdout=request.holdout or None, **common
                )
                report = result.out_of_sample or result.in_sample
                body = result.summary()
            else:
                report = run_backtest(
                    request.symbol, strategy, params=_whole(request.params), **common
                )
                body = report.summary()
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(400, str(error)) from error
        plot_id = uuid.uuid4().hex
        report.plot(plots / f"{plot_id}.html")
        return {**body, **_series(report), "plot": f"/api/plots/{plot_id}"}

    @app.get("/api/plots/{plot_id}")
    def plot(plot_id: str) -> FileResponse:
        path = plots / f"{plot_id}.html"
        if len(plot_id) != 32 or not plot_id.isalnum() or not path.exists():
            raise HTTPException(404, "Grafik bulunamadı.")
        return FileResponse(path, media_type="text/html")

    @app.post("/api/walkforward")
    def walkforward(request: WalkForwardRequest) -> dict[str, Any]:
        try:
            report = walk_forward(
                request.symbol,
                _strategy(request.strategy),
                start=request.start,
                end=request.end,
                train_bars=request.train,
                test_bars=request.test,
                cash=request.cash,
                costs=request.costs(),
                slippage=request.slippage,
            )
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(400, str(error)) from error
        curve = [
            {"t": point["time"][:10], "v": round(point["equity"], 2)}
            for point in report.equity_curve
        ]
        return {**report.summary(), "equity": _thin(curve)}

    @app.get("/api/paper")
    def paper() -> dict[str, Any]:
        if not paper_path.exists():
            return {"exists": False}
        with PaperAccount(paper_path) as account:
            curve = [
                {"t": point["time"], "v": round(point["equity"], 2)}
                for point in account.equity_curve()
            ]
            return {
                "exists": True,
                **account.summary(),
                "buying_power": round(account.buying_power(), 2),
                "created_at": account.created_at.isoformat(),
                "settings": {
                    **asdict(account.costs),
                    "slippage": account.slippage,
                    "dividend_tax": account.dividend_tax,
                },
                "equity_curve": _thin(curve),
                "orders": [o.to_dict() for o in account.orders()][-50:],
                "fills": [f.to_dict() for f in account.fills()][-50:],
            }

    @app.post("/api/paper/init")
    def paper_init(request: AccountRequest) -> dict[str, Any]:
        if paper_path.exists():
            if not request.reset:
                raise HTTPException(
                    409, "Hesap zaten var; sıfırlamak için reset seçin."
                )
            for suffix in ("", "-wal", "-shm"):
                Path(f"{paper_path}{suffix}").unlink(missing_ok=True)
        PaperAccount.create(
            paper_path,
            request.cash,
            request.costs(),
            request.slippage,
            dividend_tax=request.dividend_tax,
        ).close()
        return paper()

    def _account() -> PaperAccount:
        if not paper_path.exists():
            raise HTTPException(404, "Önce bir paper hesap açın.")
        return PaperAccount(paper_path)

    @app.post("/api/paper/order")
    def paper_order(request: OrderRequest) -> dict[str, Any]:
        price = {
            "limit_price": request.price if request.type == "limit" else None,
            "stop_price": request.price if request.type == "stop" else None,
        }
        with _account() as account:
            order = account.submit(
                request.symbol,
                request.side,
                request.qty,
                request.type,
                tif=request.tif,
                tag="web",
                **price,
            )
        return order.to_dict()

    @app.post("/api/paper/cancel/{order_id}")
    def paper_cancel(order_id: int) -> dict[str, Any]:
        with _account() as account:
            try:
                return account.cancel(order_id).to_dict()
            except KeyError as error:
                raise HTTPException(404, str(error)) from error

    @app.post("/api/paper/step")
    def paper_step(request: StepRequest) -> dict[str, Any]:
        strategy = _strategy(request.strategy) if request.strategy else None
        with _account() as account:
            trader = PaperTrader(
                account,
                request.symbols,
                ProviderFeed(request.interval),
                strategy,
                _whole(request.params),
            )
            return trader.step().to_dict()

    return app
