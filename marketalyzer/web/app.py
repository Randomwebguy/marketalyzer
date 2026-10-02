"""FastAPI app: backtests, scripts, paper trading and the AI assistant on the web.

It serves an installable app (a PWA: phone home screen or desktop window) and
a JSON API. Every request except the static files needs the access token,
because the app is meant to be reachable through a public tunnel. The login
page stores it in a cookie.
"""

import asyncio
import json
import os
import secrets
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from marketalyzer import services
from marketalyzer.ai.chat import ChatError, ChatRunner
from marketalyzer.ai.conversations import (
    delete_conversation,
    display_messages,
    list_conversations,
    load_conversation,
)
from marketalyzer.ai.openrouter import (
    OpenRouterError,
    key_info,
    list_models,
    pick_default_model,
)
from marketalyzer.ai.settings import load_settings, public_settings, save_settings
from marketalyzer.backtest.costs import BistCosts
from marketalyzer.paper.account import OrderRejected, PaperAccount
from marketalyzer.paper.cli import account_path
from marketalyzer.paper.feed import ProviderFeed
from marketalyzer.paper.trader import PaperTrader
from marketalyzer.scripting import (
    ScriptError,
    delete_script,
    get_script,
    list_scripts,
    reference,
    save_script,
)

STATIC = Path(__file__).with_name("static")
PUBLIC = (
    "/static/",
    "/login",
    "/logout",
    "/manifest.webmanifest",
    "/sw.js",
    "/favicon.ico",
)
COOKIE_DAYS = 30
KEEPALIVE_SECONDS = 15
Interval = Literal["5m", "15m", "30m", "1h", "1d", "1W"]


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
    strategy: str | None = "sma_cross"
    source: str | None = Field(None, max_length=50_000)
    start: date | None = None
    end: date | None = None
    interval: Interval = "1d"
    params: dict[str, float | str | bool] = Field(default_factory=dict)
    optimize: bool = False
    holdout: float = Field(0.3, ge=0, lt=1)
    benchmark: bool = True


class WalkForwardRequest(Costs):
    """A walk-forward test."""

    symbol: str
    strategy: str | None = "sma_cross"
    source: str | None = Field(None, max_length=50_000)
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
    params: dict[str, float | str | bool] = Field(default_factory=dict)
    interval: Literal["1d", "1h", "30m", "15m", "5m", "1m"] = "1d"


class ScriptSource(BaseModel):
    """Script source code."""

    source: str = Field(max_length=50_000)


class ScriptRun(BaseModel):
    """Run a stored script or source code on a symbol."""

    symbol: str
    name: str | None = None
    source: str | None = Field(None, max_length=50_000)
    inputs: dict[str, float | str | bool] = Field(default_factory=dict)
    span: str = "1Y"


class ScreenRequest(BaseModel):
    """Run a script on several symbols."""

    symbols: list[str] = Field(default_factory=list, max_length=30)
    name: str | None = None
    source: str | None = Field(None, max_length=50_000)
    inputs: dict[str, float | str | bool] = Field(default_factory=dict)
    interval: Interval = "1d"


class AISettingsRequest(BaseModel):
    """Changes to the assistant settings; omitted fields stay as they are."""

    api_key: str | None = Field(None, max_length=200)
    model: str | None = Field(None, max_length=200)
    allow_trading: bool | None = None
    clear_key: bool = False


class ChatRequest(BaseModel):
    """A message to the assistant."""

    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    context: dict[str, Any] | None = None


def _thread_pool(processes=None, initializer=None, initargs=()):
    """Return a thread pool with the multiprocessing.Pool interface."""
    from multiprocessing.dummy import Pool

    return Pool(processes, initializer, initargs)


def _fail(error: Exception, status: int = 400) -> HTTPException:
    if isinstance(error, ScriptError):
        return HTTPException(status, {"message": error.message, **error.to_dict()})
    return HTTPException(status, str(error))


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


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
    chat = ChatRunner(paper_path, demo)
    app.state.chat = chat

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

    @app.exception_handler(ScriptError)
    async def script_error(_: Request, error: ScriptError):
        return JSONResponse(
            {"detail": {"message": error.message, **error.to_dict()}}, status_code=400
        )

    # Pages ---------------------------------------------------------------------

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

    @app.get("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("token")
        return response

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

    # Market data ---------------------------------------------------------------

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "demo": demo,
            "account": account_name,
            "strategies": services.strategy_catalog(),
            "ranges": list(services.RANGES),
            "watchlist": list(services.WATCHLIST),
            "ai": public_settings(load_settings()),
        }

    @app.get("/api/strategies")
    def strategies() -> dict[str, Any]:
        return services.strategy_catalog()

    @app.get("/api/prices")
    def prices(symbol: str, span: str = "3A") -> dict[str, Any]:
        try:
            return services.prices(symbol, span)
        except Exception as error:
            raise _fail(error) from error

    @app.get("/api/fx")
    def fx(pair: str = "USDTRY") -> dict[str, Any]:
        try:
            return services.fx(pair)
        except Exception as error:
            raise _fail(error) from error

    @app.get("/api/watchlist")
    def watchlist(symbols: str) -> list[dict[str, Any]]:
        return services.quotes(symbols.split(","))

    @app.get("/api/analysis")
    def analysis(symbol: str, interval: Interval = "1d") -> dict[str, Any]:
        try:
            return services.technical_snapshot(symbol, interval)
        except Exception as error:
            raise _fail(error) from error

    # Backtests -----------------------------------------------------------------

    @app.post("/api/backtest")
    def backtest(request: BacktestRequest) -> dict[str, Any]:
        try:
            body, report = services.backtest(
                request.symbol,
                request.strategy,
                source=request.source,
                start=request.start,
                end=request.end,
                interval=request.interval,
                params=request.params,
                optimize=request.optimize,
                holdout=request.holdout,
                cash=request.cash,
                costs=request.costs(),
                slippage=request.slippage,
                benchmark=request.benchmark,
            )
        except Exception as error:
            raise _fail(error) from error
        plot_id = uuid.uuid4().hex
        report.plot(plots / f"{plot_id}.html")
        return {
            **body,
            **services.backtest_series(report),
            "plot": f"/api/plots/{plot_id}",
        }

    @app.get("/api/plots/{plot_id}")
    def plot(plot_id: str) -> FileResponse:
        path = plots / f"{plot_id}.html"
        if len(plot_id) != 32 or not plot_id.isalnum() or not path.exists():
            raise HTTPException(404, "Grafik bulunamadı.")
        return FileResponse(path, media_type="text/html")

    @app.post("/api/walkforward")
    def walkforward(request: WalkForwardRequest) -> dict[str, Any]:
        try:
            return services.walkforward(
                request.symbol,
                request.strategy,
                source=request.source,
                start=request.start,
                end=request.end,
                train=request.train,
                test=request.test,
                cash=request.cash,
                costs=request.costs(),
                slippage=request.slippage,
            )
        except Exception as error:
            raise _fail(error) from error

    # Scripts -------------------------------------------------------------------

    @app.get("/api/scripts")
    def scripts() -> list[dict[str, Any]]:
        return list_scripts()

    @app.get("/api/scripts/reference")
    def script_reference() -> list[dict[str, Any]]:
        return reference()

    @app.post("/api/scripts/check")
    def script_check(request: ScriptSource) -> dict[str, Any]:
        return services.check_script(request.source)

    @app.post("/api/scripts/run")
    def script_run(request: ScriptRun) -> dict[str, Any]:
        try:
            return services.run_script(
                request.symbol,
                name=request.name,
                source=request.source,
                inputs=request.inputs,
                span=request.span,
            )
        except Exception as error:
            raise _fail(error) from error

    @app.post("/api/scripts/screen")
    def script_screen(request: ScreenRequest) -> dict[str, Any]:
        try:
            return services.screen(
                request.symbols,
                name=request.name,
                source=request.source,
                inputs=request.inputs,
                interval=request.interval,
            )
        except Exception as error:
            raise _fail(error) from error

    @app.get("/api/scripts/{name}")
    def script(name: str) -> dict[str, Any]:
        try:
            return get_script(name)
        except (KeyError, ValueError) as error:
            raise HTTPException(404, "Script bulunamadı.") from error

    @app.put("/api/scripts/{name}")
    def script_save(name: str, request: ScriptSource) -> dict[str, Any]:
        try:
            return save_script(name, request.source)
        except ValueError as error:
            raise _fail(error) from error

    @app.delete("/api/scripts/{name}")
    def script_delete(name: str) -> dict[str, Any]:
        try:
            return {"deleted": name, "restored_builtin": delete_script(name)}
        except KeyError as error:
            raise HTTPException(404, "Script bulunamadı.") from error
        except ValueError as error:
            raise _fail(error) from error

    # Paper trading -------------------------------------------------------------

    @app.get("/api/paper")
    def paper() -> dict[str, Any]:
        return services.paper_status(paper_path)

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
        try:
            strategy = (
                services.resolve_strategy(request.strategy)
                if request.strategy
                else None
            )
        except Exception as error:
            raise _fail(error) from error
        with _account() as account:
            trader = PaperTrader(
                account,
                request.symbols,
                ProviderFeed(request.interval),
                strategy,
                services.whole(request.params),
            )
            return trader.step().to_dict()

    # Assistant -----------------------------------------------------------------

    @app.get("/api/ai/settings")
    def ai_settings() -> dict[str, Any]:
        return public_settings(load_settings())

    @app.post("/api/ai/settings")
    def ai_settings_save(request: AISettingsRequest) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if request.api_key:
            changes["api_key"] = request.api_key
        if request.model is not None:
            changes["model"] = request.model
        if request.allow_trading is not None:
            changes["allow_trading"] = request.allow_trading
        try:
            settings = save_settings(**changes, clear_key=request.clear_key)
        except ValueError as error:
            raise _fail(error) from error
        return public_settings(settings)

    @app.get("/api/ai/models")
    def ai_models(refresh: bool = False) -> dict[str, Any]:
        settings = load_settings()
        try:
            models = list_models(settings.api_key, refresh=refresh)
        except OpenRouterError as error:
            raise HTTPException(502, error.message) from error
        return {"models": models, "default": pick_default_model(models)}

    @app.post("/api/ai/test")
    def ai_test() -> dict[str, Any]:
        settings = load_settings()
        if not settings.api_key:
            raise HTTPException(400, "Önce bir OpenRouter API anahtarı kaydedin.")
        try:
            return {"ok": True, **key_info(settings.api_key)}
        except OpenRouterError as error:
            raise HTTPException(502, error.message) from error

    @app.get("/api/ai/conversations")
    def ai_conversations() -> list[dict[str, Any]]:
        return list_conversations()

    @app.get("/api/ai/conversations/{conversation_id}")
    def ai_conversation(conversation_id: str) -> dict[str, Any]:
        try:
            conversation = load_conversation(conversation_id)
        except KeyError as error:
            raise HTTPException(404, "Sohbet bulunamadı.") from error
        return {
            "id": conversation["id"],
            "title": conversation["title"],
            "usage": conversation.get("usage"),
            "model": conversation.get("model"),
            "running": chat.running(conversation["id"]),
            "messages": display_messages(conversation),
        }

    @app.delete("/api/ai/conversations/{conversation_id}")
    def ai_conversation_delete(conversation_id: str) -> dict[str, Any]:
        chat.stop(conversation_id)
        try:
            delete_conversation(conversation_id)
        except KeyError as error:
            raise HTTPException(404, "Sohbet bulunamadı.") from error
        return {"deleted": conversation_id}

    @app.post("/api/ai/stop/{conversation_id}")
    def ai_stop(conversation_id: str) -> dict[str, Any]:
        return {"stopped": chat.stop(conversation_id)}

    @app.post("/api/ai/chat")
    async def ai_chat(request: ChatRequest) -> StreamingResponse:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        try:
            conversation, cancel = chat.start(
                request.message, emit, request.conversation_id, request.context
            )
        except ChatError as error:
            raise HTTPException(error.status, error.message) from error

        async def events():
            try:
                yield _sse(
                    {
                        "type": "start",
                        "conversation_id": conversation["id"],
                        "title": conversation["title"],
                    }
                )
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        # Keeps proxies such as Cloudflare from closing a quiet stream.
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(event)
                    if event.get("type") == "done":
                        break
            finally:
                # The client went away or the turn ended; stop any work left.
                cancel.set()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
