"""FastAPI app: the backtest, walk-forward and paper trading tools in a browser.

Every request needs the access token, because the app is meant to be reachable
through a public tunnel. Open ``/?token=...`` once; the browser keeps it in a
cookie.
"""

import os
import re
import secrets
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.engine import optimize_backtest, run_backtest
from marketalyzer.backtest.strategies import STRATEGIES, strategy_params
from marketalyzer.paper.account import OrderRejected, PaperAccount
from marketalyzer.paper.cli import account_path
from marketalyzer.paper.feed import ProviderFeed
from marketalyzer.paper.trader import PaperTrader
from marketalyzer.walkforward import walk_forward

PAGE = Path(__file__).with_name("page.html")
PLOT_ID = re.compile(r"^[0-9a-f]{32}$")


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


def _thread_pool(processes=None, initializer=None, initargs=()):
    """Return a thread pool with the multiprocessing.Pool interface."""
    from multiprocessing.dummy import Pool

    return Pool(processes, initializer, initargs)


def create_app(token: str | None = None, account_name: str = "web") -> FastAPI:
    """Build the app. ``account_name`` names the paper account it manages."""
    import backtesting

    # Requests run in server threads, and backtesting.py optimizes in processes
    # it forks; forking a multi-threaded process can deadlock. Threads are slower
    # but safe here.
    backtesting.Pool = _thread_pool
    token = token or os.environ.get("MARKETALYZER_TOKEN") or secrets.token_urlsafe(16)
    app = FastAPI(title="marketalyzer", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    plots = Path(tempfile.mkdtemp(prefix="marketalyzer-plots-"))
    paper_path = account_path(account_name)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        header = request.headers.get("authorization", "")
        supplied = (
            request.query_params.get("token")
            or request.cookies.get("token")
            or header.removeprefix("Bearer ").strip()
        )
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            return JSONResponse(
                {
                    "detail": "Erişim anahtarı gerekli: adresin sonuna ?token=... ekleyin."
                },
                status_code=401,
            )
        response = await call_next(request)
        if request.query_params.get("token"):
            secure = request.headers.get("x-forwarded-proto", request.url.scheme)
            response.set_cookie(
                "token",
                token,
                httponly=True,
                samesite="strict",
                secure=secure == "https",
            )
        return response

    @app.exception_handler(OrderRejected)
    async def rejected(_: Request, error: OrderRejected):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/strategies")
    def strategies() -> dict[str, Any]:
        return {
            name: {
                "doc": (cls.__doc__ or "").strip(),
                "params": strategy_params(cls),
                "grid": getattr(cls, "param_grid", {}),
            }
            for name, cls in STRATEGIES.items()
        }

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
        return {**body, "plot": f"/api/plots/{plot_id}"}

    @app.get("/api/plots/{plot_id}")
    def plot(plot_id: str) -> FileResponse:
        path = plots / f"{plot_id}.html"
        if not PLOT_ID.match(plot_id) or not path.exists():
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
        return report.summary()

    @app.get("/api/paper")
    def paper() -> dict[str, Any]:
        if not paper_path.exists():
            return {"exists": False}
        with PaperAccount(paper_path) as account:
            return {
                "exists": True,
                **account.summary(),
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
