"""The assistant's tools: market data, analysis, scripts, backtests and paper trading.

Each tool wraps a function in :mod:`marketalyzer.services` and returns a compact
result, since every tool result is sent back to the model. Paper orders are only
allowed when the person enabled them in the settings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marketalyzer import services
from marketalyzer.ai.agent import ToolError
from marketalyzer.ai.settings import load_settings
from marketalyzer.paper.account import OrderRejected, PaperAccount
from marketalyzer.scripting import ScriptError, get_script, list_scripts, save_script

INDICES = ("XU100", "XU030", "XBANK", "XUSIN")
MAX_TRADES = 12


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


SYMBOL = {"type": "string", "description": "BIST kodu, ör. THYAO, GARAN, XU100."}
DATE = {"type": "string", "description": "YYYY-AA-GG"}
INTERVAL = {"type": "string", "enum": list(services.INTERVALS), "default": "1d"}
STRATEGY = {
    "type": "string",
    "description": "Strateji adı: sma_cross, rsi_reversion ya da script:<ad>."
    " Kaydedilmemiş bir script için bunun yerine source verin.",
}
SOURCE = {
    "type": "string",
    "description": "Script kaynak kodu (Pine benzeri). Verilirse strategy/name yok sayılır.",
}
PARAMS = {
    "type": "object",
    "description": 'Parametre değerleri, ör. {"fastLength": 10}.',
    "additionalProperties": {"type": ["number", "string", "boolean"]},
}
GRID = {
    "type": "object",
    "description": 'Optimizasyon ızgarası, ör. {"length": [10, 14, 20]}. Verilmezse'
    " scriptin minval/maxval/step değerleri kullanılır.",
    "additionalProperties": {"type": "array", "items": {"type": ["number", "string"]}},
}


@dataclass
class Tool:
    """One tool: its schema for the model and the function that runs it."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., Any]

    def spec(self) -> dict[str, Any]:
        """Return the OpenAI function-calling spec."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _trades(trades: list[dict]) -> dict[str, Any]:
    if len(trades) <= MAX_TRADES:
        return {"trades_shown": trades}
    return {
        "trades_shown": trades[-MAX_TRADES:],
        "trades_omitted": len(trades) - MAX_TRADES,
    }


class Tools:
    """The tool registry handed to :func:`marketalyzer.ai.agent.run_turn`.

    Parameters
    ----------
    paper_path
        The web app's paper account file.
    demo
        Whether prices are synthetic demo data.
    on_event
        Called with UI hints such as ``{"type": "script_saved", "name": ...}``.
    """

    def __init__(
        self,
        paper_path: Path,
        demo: bool = False,
        on_event: Callable[[dict], None] | None = None,
    ):
        self.paper_path = paper_path
        self.demo = demo
        self.on_event = on_event or (lambda event: None)
        self.tools = {tool.name: tool for tool in self._tools()}

    def specs(self) -> list[dict[str, Any]]:
        """Return every tool's spec."""
        return [tool.spec() for tool in self.tools.values()]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        """Run a tool, turning user-level errors into ``ToolError``."""
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"Bilinmeyen araç: {name}")
        try:
            return tool.run(**args)
        except TypeError as error:
            raise ToolError(f"Geçersiz argümanlar: {error}") from None
        except (ScriptError, ValueError, KeyError, OrderRejected) as error:
            message = (
                str(error)
                if not isinstance(error, KeyError)
                else f"Bulunamadı: {error}"
            )
            raise ToolError(message) from None

    # Tools ---------------------------------------------------------------------

    def _tools(self) -> list[Tool]:
        return [
            Tool(
                "market_overview",
                "BIST endekslerinin (XU100, XU030, XBANK, XUSIN) ve izleme listesindeki"
                " hisselerin son fiyat ve günlük değişimi.",
                _obj({}),
                self.market_overview,
            ),
            Tool(
                "get_quotes",
                "Hisselerin son fiyatı, günlük değişimi, hacmi ve 30 günlük kapanışları.",
                _obj(
                    {"symbols": {"type": "array", "items": SYMBOL, "maxItems": 12}},
                    ["symbols"],
                ),
                services.quotes,
            ),
            Tool(
                "price_history",
                "Bir sembolün seçilen tarih aralığındaki fiyat istatistikleri (getiri,"
                " en yüksek/düşük, maksimum düşüş, oynaklık) ve son barları.",
                _obj(
                    {
                        "symbol": SYMBOL,
                        "interval": INTERVAL,
                        "start": DATE,
                        "end": DATE,
                        "bars": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 120,
                            "default": 30,
                        },
                    },
                    ["symbol"],
                ),
                self.price_history,
            ),
            Tool(
                "technical_analysis",
                "Son bardaki teknik gösterge okumaları: hareketli ortalamalar, RSI, MACD,"
                " Bollinger, ATR, ADX/DMI, stokastik, supertrend, hacim, pivot destek/"
                "direnç ve özet sinyaller.",
                _obj({"symbol": SYMBOL, "interval": INTERVAL}, ["symbol"]),
                lambda symbol, interval="1d": services.technical_snapshot(
                    symbol, interval
                ),
            ),
            Tool(
                "list_strategies",
                "Backtest/paper trading için kullanılabilecek stratejiler, parametreleri"
                " ve optimizasyon ızgaraları.",
                _obj({}),
                self.list_strategies,
            ),
            Tool(
                "list_scripts",
                "Kayıtlı ve hazır scriptler (gösterge ve strateji), açıklamaları ve"
                " parametreleri.",
                _obj({}),
                self.list_scripts,
            ),
            Tool(
                "get_script",
                "Bir scriptin kaynak kodu ve parametreleri.",
                _obj({"name": {"type": "string"}}, ["name"]),
                self.get_script,
            ),
            Tool(
                "check_script",
                "Script kodunu derler; hata varsa satır numarasıyla döndürür, yoksa"
                " bildirimi ve parametreleri.",
                _obj({"source": SOURCE}, ["source"]),
                services.check_script,
            ),
            Tool(
                "save_script",
                "Scripti verilen adla kaydeder (küçük harf, rakam, - ve _). Aynı adlı"
                " script varsa üzerine yazar. Kullanıcı kaydetmek isterse kullanın.",
                _obj(
                    {"name": {"type": "string"}, "source": SOURCE}, ["name", "source"]
                ),
                self.save_script,
            ),
            Tool(
                "run_script",
                "Bir scripti sembol üzerinde çalıştırır; çizimlerin son değerlerini,"
                " son giriş/çıkış sinyallerini ve alarmları döndürür.",
                _obj(
                    {
                        "symbol": SYMBOL,
                        "name": {
                            "type": "string",
                            "description": "Kayıtlı script adı.",
                        },
                        "source": SOURCE,
                        "inputs": PARAMS,
                        "interval": INTERVAL,
                        "start": DATE,
                        "end": DATE,
                    },
                    ["symbol"],
                ),
                self.run_script,
            ),
            Tool(
                "screen_symbols",
                "Bir scripti birden çok hissede (varsayılan: likit BIST hisseleri)"
                " çalıştırıp son bardaki sinyalleri ve değerleri listeler.",
                _obj(
                    {
                        "symbols": {"type": "array", "items": SYMBOL, "maxItems": 30},
                        "name": {"type": "string"},
                        "source": SOURCE,
                        "inputs": PARAMS,
                        "interval": INTERVAL,
                    }
                ),
                self.screen,
            ),
            Tool(
                "backtest",
                "Stratejiyi BIST maliyetleriyle (komisyon+BSMV, kayma) geçmiş veride test"
                " eder; getiri, Sharpe, maksimum düşüş, işlem sayısı, al-tut, XU100 ve USD"
                " karşılaştırmasıyla döndürür. Varsayılan dönem son 2 yıl.",
                _obj(
                    {
                        "symbol": SYMBOL,
                        "strategy": STRATEGY,
                        "source": SOURCE,
                        "params": PARAMS,
                        "start": DATE,
                        "end": DATE,
                        "interval": INTERVAL,
                        "cash": {"type": "number", "default": 100000},
                    },
                    ["symbol"],
                ),
                self.backtest,
            ),
            Tool(
                "optimize_strategy",
                "Parametreleri dönemin ilk kısmında optimize eder, en iyileri görülmemiş"
                " son kısımda (holdout) test eder. Aşırı uyumu görmek için iki sonucu"
                " karşılaştırın.",
                _obj(
                    {
                        "symbol": SYMBOL,
                        "strategy": STRATEGY,
                        "source": SOURCE,
                        "param_grid": GRID,
                        "maximize": {
                            "type": "string",
                            "enum": [
                                "Sharpe Ratio",
                                "Return [%]",
                                "Sortino Ratio",
                                "Calmar Ratio",
                                "Win Rate [%]",
                                "Profit Factor",
                            ],
                            "default": "Sharpe Ratio",
                        },
                        "holdout": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 0.6,
                            "default": 0.3,
                        },
                        "start": DATE,
                        "end": DATE,
                    },
                    ["symbol"],
                ),
                self.optimize,
            ),
            Tool(
                "walk_forward",
                "Kayan pencerelerle walk-forward testi: her pencerede geçmişte optimize"
                " eder, sonraki dönemi sanal hesapta işler. Gerçekçi, ileriye dönük"
                " performans tahmini verir. Varsayılan son 5 yıl.",
                _obj(
                    {
                        "symbol": SYMBOL,
                        "strategy": STRATEGY,
                        "source": SOURCE,
                        "param_grid": GRID,
                        "start": DATE,
                        "end": DATE,
                        "train": {
                            "type": "integer",
                            "minimum": 60,
                            "default": 504,
                            "description": "Eğitim penceresi (bar).",
                        },
                        "test": {
                            "type": "integer",
                            "minimum": 10,
                            "default": 126,
                            "description": "Test penceresi (bar).",
                        },
                    },
                    ["symbol"],
                ),
                self.walk_forward,
            ),
            Tool(
                "paper_account",
                "Sanal (paper) hesabın nakit, özsermaye, getiri, pozisyonlar, açık emirler"
                " ve son işlemleri.",
                _obj({}),
                self.paper_account,
            ),
            Tool(
                "paper_order",
                "Sanal hesapta emir verir (gerçek para yok). Yalnızca kullanıcı ayarlardan"
                " izin verdiyse çalışır ve kullanıcı açıkça istediğinde kullanılmalıdır."
                " Piyasa emri bir sonraki barın açılışında dolar.",
                _obj(
                    {
                        "symbol": SYMBOL,
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                        "qty": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Lot (adet).",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["market", "limit", "stop"],
                            "default": "market",
                        },
                        "price": {
                            "type": "number",
                            "description": "Limit/stop fiyatı (BIST fiyat adımına uygun).",
                        },
                        "tif": {
                            "type": "string",
                            "enum": ["day", "gtc"],
                            "default": "day",
                        },
                    },
                    ["symbol", "side", "qty"],
                ),
                self.paper_order,
            ),
            Tool(
                "paper_cancel",
                "Sanal hesaptaki açık bir emri iptal eder (ayarlarda izin gerekir).",
                _obj({"order_id": {"type": "integer"}}, ["order_id"]),
                self.paper_cancel,
            ),
        ]

    def market_overview(self) -> dict[str, Any]:
        """Return indices and the watchlist, biggest movers first."""
        indices = services.quotes(list(INDICES))
        stocks = services.quotes(list(services.WATCHLIST))
        valid = [q for q in stocks if "error" not in q]
        valid.sort(key=lambda q: q["change_pct"], reverse=True)
        compact = [
            {
                key: q.get(key)
                for key in ("symbol", "last", "change_pct", "date", "error")
            }
            for q in (*indices, *valid)
        ]
        return {
            "indices": compact[: len(indices)],
            "stocks": compact[len(indices) :],
            "demo": self.demo,
        }

    def price_history(self, symbol, interval="1d", start=None, end=None, bars=30):
        """Statistics and recent bars."""
        return services.history_summary(symbol, interval, start, end, limit=bars)

    def list_strategies(self) -> dict[str, Any]:
        """Strategies with parameters and grid sizes."""
        return {
            name: {
                "label": entry["label"],
                "kind": entry["kind"],
                "params": entry["params"],
                "grid": entry["grid"],
                "doc": entry["doc"][:300],
            }
            for name, entry in services.strategy_catalog().items()
        }

    def list_scripts(self) -> list[dict[str, Any]]:
        """Scripts without their source."""
        return [
            {
                "name": entry["name"],
                "title": entry["title"],
                "kind": entry["kind"],
                "builtin": entry["builtin"],
                "description": entry["description"][:240],
                "inputs": {i["name"]: i["default"] for i in entry["inputs"]},
                "error": entry["error"],
            }
            for entry in list_scripts()
        ]

    def get_script(self, name: str) -> dict[str, Any]:
        """Return a script's source."""
        entry = get_script(name.removeprefix("script:"))
        return {
            "name": entry["name"],
            "title": entry["title"],
            "kind": entry["kind"],
            "source": entry["source"],
            "inputs": entry["inputs"],
            "error": entry["error"],
        }

    def save_script(self, name: str, source: str) -> dict[str, Any]:
        """Save a script and tell the UI."""
        entry = save_script(name, source)
        self.on_event({"type": "script_saved", "name": entry["name"]})
        return {
            "saved": entry["name"],
            "kind": entry["kind"],
            "error": entry["error"],
            "strategy_name": f"script:{entry['name']}"
            if entry["kind"] == "strategy"
            else None,
        }

    def run_script(
        self,
        symbol,
        name=None,
        source=None,
        inputs=None,
        interval="1d",
        start=None,
        end=None,
    ):
        """Run a script and summarize its output."""
        payload = services.run_script(
            symbol,
            name=name,
            source=source,
            inputs=inputs,
            span=None if start else "1Y",
            start=start,
            end=end,
            interval=interval,
        )
        return services.script_summary(payload)

    def screen(self, symbols=None, name=None, source=None, inputs=None, interval="1d"):
        """Run a script over many symbols."""
        if not name and not source:
            raise ValueError(
                "Taramak için bir script adı (name) ya da kaynak (source) verin."
            )
        return services.screen(
            symbols, name=name, source=source, inputs=inputs, interval=interval
        )

    def backtest(
        self,
        symbol,
        strategy=None,
        source=None,
        params=None,
        start=None,
        end=None,
        interval="1d",
        cash=100_000,
    ):
        """Backtest and summarize."""
        body, report = services.backtest(
            symbol,
            strategy or ("sma_cross" if not source else None),
            source=source,
            params=params,
            start=start,
            end=end,
            interval=interval,
            cash=cash,
        )
        trades = services.backtest_series(report)["trade_list"]
        self.on_event(
            {
                "type": "backtest",
                "symbol": symbol,
                "strategy": strategy,
                "params": body.get("params"),
            }
        )
        return {**body, **_trades(trades)}

    def optimize(
        self,
        symbol,
        strategy=None,
        source=None,
        param_grid=None,
        maximize="Sharpe Ratio",
        holdout=0.3,
        start=None,
        end=None,
    ):
        """Optimize with a holdout period."""
        body, _ = services.backtest(
            symbol,
            strategy or ("sma_cross" if not source else None),
            source=source,
            optimize=True,
            param_grid=param_grid,
            maximize=maximize,
            holdout=holdout,
            start=start,
            end=end,
        )
        return body

    def walk_forward(
        self,
        symbol,
        strategy=None,
        source=None,
        param_grid=None,
        start=None,
        end=None,
        train=504,
        test=126,
    ):
        """Walk-forward test."""
        body = services.walkforward(
            symbol,
            strategy or ("sma_cross" if not source else None),
            source=source,
            param_grid=param_grid,
            start=start,
            end=end,
            train=train,
            test=test,
        )
        body.pop("equity", None)
        return body

    def paper_account(self) -> dict[str, Any]:
        """Return the paper account summary."""
        status = services.paper_status(self.paper_path, detail=False)
        status["trading_allowed"] = load_settings().allow_trading
        return status

    def _require_trading(self) -> None:
        if not load_settings().allow_trading:
            raise ToolError(
                "Asistanın sanal emir vermesine izin verilmemiş. Kullanıcı Ayarlar >"
                " Yapay zeka bölümünden izin verebilir ya da emri İşlem ekranından"
                " kendisi girebilir."
            )
        if not self.paper_path.exists():
            raise ToolError(
                "Henüz sanal hesap yok; kullanıcı İşlem ekranından açabilir."
            )

    def paper_order(self, symbol, side, qty, type="market", price=None, tif="day"):  # noqa: A002
        """Submit a paper order."""
        self._require_trading()
        if type != "market" and price is None:
            raise ToolError("Limit ve stop emirleri için price gerekli.")
        with PaperAccount(self.paper_path) as account:
            order = account.submit(
                symbol,
                side,
                int(qty),
                type,
                limit_price=price if type == "limit" else None,
                stop_price=price if type == "stop" else None,
                tif=tif,
                tag="ai",
            )
        self.on_event({"type": "paper_changed"})
        return order.to_dict()

    def paper_cancel(self, order_id: int) -> dict[str, Any]:
        """Cancel a paper order."""
        self._require_trading()
        with PaperAccount(self.paper_path) as account:
            order = account.cancel(int(order_id))
        self.on_event({"type": "paper_changed"})
        return order.to_dict()
