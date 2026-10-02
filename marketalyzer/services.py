"""Operations shared by the web API and the AI assistant's tools.

Everything here returns JSON-friendly dicts. Errors a person can fix (unknown
symbol, bad script, bad parameter) are raised as ``ValueError`` or
``ScriptError`` with a Turkish message.
"""

from __future__ import annotations

import math
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from backtesting import Strategy

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.data import load_fx, load_ohlcv
from marketalyzer.backtest.engine import BacktestReport, optimize_backtest, run_backtest
from marketalyzer.backtest.strategies import STRATEGIES, get_strategy, strategy_params
from marketalyzer.paper.account import PaperAccount, normalize_symbol
from marketalyzer.paper.models import IST
from marketalyzer.scripting import (
    ScriptError,
    compile_script,
    list_scripts,
    load_script,
    script_strategy,
    ta,
)
from marketalyzer.walkforward import walk_forward
from openbb_bist.utils.constants import BIST_INDICES, INTRADAY_LOOKBACK_DAYS

MAX_POINTS = 400
MAX_SCREEN = 30
MAX_COMBINATIONS = 300
# Range code -> (bar interval, calendar days shown).
RANGES = {
    "1G": ("5m", 5),
    "1H": ("1h", 9),
    "1A": ("1d", 31),
    "3A": ("1d", 92),
    "6A": ("1d", 183),
    "1Y": ("1d", 366),
    "5Y": ("1W", 1830),
}
# Extra calendar days loaded before a chart so indicators are warmed up.
WARMUP_DAYS = {"5m": 20, "15m": 30, "30m": 40, "1h": 120, "1d": 600, "1W": 2500}
INTERVALS = ("5m", "15m", "30m", "1h", "1d", "1W")
WATCHLIST = (
    "THYAO",
    "GARAN",
    "AKBNK",
    "ASELS",
    "BIMAS",
    "EREGL",
    "KCHOL",
    "SISE",
    "TUPRS",
    "YKBNK",
)


# --- Small helpers ------------------------------------------------------------


def thin(items: list[Any], limit: int = MAX_POINTS) -> list[Any]:
    """Keep at most ``limit`` evenly spaced items, always including the last."""
    if len(items) <= limit:
        return items
    step = len(items) / limit
    picked = [items[int(i * step)] for i in range(limit - 1)]
    return [*picked, items[-1]]


def stamp(value: Any) -> str:
    """Return a date, or a date and time for intraday bars, as ISO text."""
    moment = pd.Timestamp(value)
    if moment.hour == moment.minute == 0:
        return moment.date().isoformat()
    return moment.isoformat()


def whole(params: dict[str, Any]) -> dict[str, Any]:
    """Turn 10.0 into 10, so integer strategy parameters stay integers."""
    return {
        key: int(value) if isinstance(value, float) and value.is_integer() else value
        for key, value in params.items()
    }


def number(value: Any, digits: int = 4) -> float | None:
    """Round a number for JSON; ``nan`` and infinities become None."""
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def today() -> date:
    """Today's date in Istanbul."""
    return datetime.now(IST).date()


def _parse_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(
            f"Geçersiz tarih: {value} (YYYY-AA-GG bekleniyordu)."
        ) from None


def load_bars(
    symbol: str,
    start: date | str | None = None,
    end: date | str | None = None,
    interval: str = "1d",
    *,
    warmup: bool = False,
) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Load split-adjusted bars; with ``warmup``, also load earlier bars.

    Returns the frame and the first bar time to display (None without warmup).
    """
    if interval not in INTERVALS:
        raise ValueError(
            f"Geçersiz zaman dilimi: {interval}. Seçenekler: {', '.join(INTERVALS)}."
        )
    end = _parse_date(end) or today()
    start = _parse_date(start) or end - timedelta(days=365)
    if start >= end:
        raise ValueError("Başlangıç tarihi bitişten önce olmalı.")
    first = start
    if warmup:
        first = start - timedelta(days=WARMUP_DAYS.get(interval, 600))
    limit = INTRADAY_LOOKBACK_DAYS.get(interval)
    if limit:
        first = max(first, today() - timedelta(days=limit))
        start = max(start, first)
    frame = load_ohlcv(
        normalize_symbol(symbol), first, end, interval, "splits_only", cache=False
    )
    if frame.empty:
        raise ValueError(f"{symbol} için bu aralıkta veri yok.")
    shown = None
    if warmup:
        index = pd.DatetimeIndex(frame.index)
        later = index[index >= pd.Timestamp(start, tz=index.tz)]
        shown = later[0] if len(later) else index[0]
    return frame, shown


def span_window(span: str) -> tuple[str, date, date]:
    """Return the interval and dates of a chart range code such as ``3A``."""
    if span not in RANGES:
        raise ValueError(f"Geçersiz aralık: {span}. Seçenekler: {', '.join(RANGES)}.")
    interval, days = RANGES[span]
    end = today()
    return interval, end - timedelta(days=days), end


# --- Quotes and prices ----------------------------------------------------------


def quote(symbol: str) -> dict[str, Any]:
    """Last price, day change and a 30-day sparkline from daily bars."""
    code = normalize_symbol(symbol)
    end = today()
    frame = load_ohlcv(
        code, end - timedelta(days=60), end, adjustment="splits_only", cache=False
    )
    close = frame["Close"]
    previous = (
        float(close.iloc[-2]) if len(close) > 1 else float(frame["Open"].iloc[-1])
    )
    last = float(close.iloc[-1])
    volume = frame.get("Volume")
    return {
        "symbol": code,
        "name": BIST_INDICES.get(code),
        "last": last,
        "change_pct": round((last / previous - 1) * 100, 2),
        "volume": number(volume.iloc[-1], 0) if volume is not None else None,
        "date": frame.index[-1].date().isoformat(),
        "spark": [round(float(v), 4) for v in close.iloc[-30:]],
    }


def fx(pair: str = "USDTRY") -> dict[str, Any]:
    """Return the last daily close of a currency pair, to show amounts in USD."""
    end = today()
    frame = load_fx(pair, end - timedelta(days=20), end, cache=False)
    close = frame["Close"]
    previous = float(close.iloc[-2]) if len(close) > 1 else float(close.iloc[-1])
    return {
        "pair": pair.upper(),
        "last": number(close.iloc[-1]),
        "change_pct": number((close.iloc[-1] / previous - 1) * 100, 2),
        "date": stamp(frame.index[-1]),
    }


def quotes(symbols: list[str], limit: int = 12) -> list[dict[str, Any]]:
    """Quotes for several symbols, loaded in parallel; failures carry ``error``."""
    codes = list(dict.fromkeys(normalize_symbol(s) for s in symbols if s.strip()))
    codes = codes[:limit]
    if not codes:
        return []

    def one(code: str) -> dict[str, Any]:
        try:
            return quote(code)
        except Exception as error:
            return {"symbol": code, "error": str(error)}

    with ThreadPoolExecutor(max_workers=min(6, len(codes))) as pool:
        return list(pool.map(one, codes))


def candles(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """OHLCV rows for charts."""
    volume = frame["Volume"] if "Volume" in frame else pd.Series(0.0, frame.index)
    return [
        {
            "t": stamp(t),
            "o": number(o),
            "h": number(h),
            "l": number(low),
            "c": number(c),
            "v": number(v, 0),
        }
        for t, o, h, low, c, v in zip(
            frame.index,
            frame["Open"],
            frame["High"],
            frame["Low"],
            frame["Close"],
            volume,
        )
    ]


def prices(symbol: str, span: str = "3A") -> dict[str, Any]:
    """Candles for a chart range."""
    interval, start, end = span_window(span)
    code = normalize_symbol(symbol)
    frame, _ = load_bars(code, start, end, interval)
    if span == "1G":
        frame = frame[frame.index.date == frame.index[-1].date()]
    rows = candles(frame)
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


def history_summary(
    symbol: str,
    interval: str = "1d",
    start: date | str | None = None,
    end: date | str | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    """Price statistics and the last ``limit`` bars, compact enough for the AI."""
    frame, _ = load_bars(symbol, start, end, interval)
    close = frame["Close"]
    returns = close.pct_change().dropna()
    peak = close.cummax()
    periods = {"1d": 252, "1W": 52}.get(interval)
    volatility = (
        float(returns.std() * math.sqrt(periods)) * 100
        if periods and len(returns) > 2
        else None
    )
    rows = candles(frame.iloc[-limit:])
    return {
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "start": stamp(frame.index[0]),
        "end": stamp(frame.index[-1]),
        "bars": len(frame),
        "first_close": number(close.iloc[0]),
        "last_close": number(close.iloc[-1]),
        "return_pct": number((close.iloc[-1] / close.iloc[0] - 1) * 100, 2),
        "high": number(frame["High"].max()),
        "low": number(frame["Low"].min()),
        "max_drawdown_pct": number(((close / peak - 1).min()) * 100, 2),
        "volatility_ann_pct": number(volatility, 2),
        "average_volume": number(frame["Volume"].mean(), 0)
        if "Volume" in frame
        else None,
        "recent_bars": rows,
    }


# --- Technical snapshot ------------------------------------------------------------


def _last(values: np.ndarray, digits: int = 2) -> float | None:
    return number(values[-1], digits) if len(values) else None


def _change(close: np.ndarray, bars: int) -> float | None:
    if len(close) <= bars:
        return None
    return number((close[-1] / close[-1 - bars] - 1) * 100, 2)


def technical_snapshot(symbol: str, interval: str = "1d") -> dict[str, Any]:
    """Key indicator readings on the last bar, with plain-language signals."""
    end = today()
    days = 900 if interval == "1d" else 3000 if interval == "1W" else 59
    frame, _ = load_bars(symbol, end - timedelta(days=days), end, interval)
    o, h, low, c = (
        frame[col].to_numpy(float) for col in ("Open", "High", "Low", "Close")
    )
    v = frame["Volume"].to_numpy(float) if "Volume" in frame else np.zeros(len(c))
    if len(c) < 30:
        raise ValueError(f"{symbol}: teknik özet için en az 30 bar gerekli.")
    sma20, sma50, sma200 = ta.sma(c, 20), ta.sma(c, 50), ta.sma(c, 200)
    rsi = ta.rsi(c, 14)
    macd, signal, hist = ta.macd(c, 12, 26, 9)
    basis, upper, lower = ta.bb(c, 20, 2)
    atr = ta.atr(h, low, c, 14)
    plus, minus, adx = ta.dmi(h, low, c, 14, 14)
    k = ta.sma(ta.stoch(c, h, low, 14), 3)
    d = ta.sma(k, 3)
    st_line, st_dir = ta.supertrend(h, low, c, 3, 10)
    obv = ta.obv(c, v)
    window = c[-252:]
    last = c[-1]
    signals = []
    for name, average in (("SMA50", sma50), ("SMA200", sma200)):
        if not math.isnan(average[-1]):
            side = "üstünde" if last > average[-1] else "altında"
            signals.append(f"Fiyat {name} {side}")
    if not math.isnan(sma50[-1]) and not math.isnan(sma200[-1]):
        cross = (
            "golden cross (SMA50 > SMA200)"
            if sma50[-1] > sma200[-1]
            else "death cross bölgesi (SMA50 < SMA200)"
        )
        signals.append(cross)
    if rsi[-1] >= 70:
        signals.append("RSI aşırı alım bölgesinde (≥70)")
    elif rsi[-1] <= 30:
        signals.append("RSI aşırı satım bölgesinde (≤30)")
    if not math.isnan(hist[-1]) and not math.isnan(hist[-2]):
        if hist[-1] > 0 >= hist[-2]:
            signals.append("MACD bu bar sinyal çizgisini yukarı kesti")
        elif hist[-1] < 0 <= hist[-2]:
            signals.append("MACD bu bar sinyal çizgisini aşağı kesti")
    if not math.isnan(adx[-1]):
        strength = "güçlü trend" if adx[-1] >= 25 else "zayıf/yatay trend"
        signals.append(f"ADX {adx[-1]:.0f}: {strength}")
    if not math.isnan(st_dir[-1]):
        signals.append(
            "Supertrend yükseliş yönünde"
            if st_dir[-1] < 0
            else "Supertrend düşüş yönünde"
        )
    volume_ratio = (
        number(v[-1] / np.nanmean(v[-21:-1]), 2)
        if len(v) > 21 and np.nanmean(v[-21:-1])
        else None
    )
    highs = ta.pivothigh(h, 5, 5)
    lows = ta.pivotlow(low, 5, 5)
    resistance = [number(x) for x in highs[~np.isnan(highs)][-3:]]
    support = [number(x) for x in lows[~np.isnan(lows)][-3:]]
    width = (upper[-1] - lower[-1]) if not math.isnan(upper[-1]) else math.nan
    return {
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "date": stamp(frame.index[-1]),
        "close": number(last),
        "open": number(o[-1]),
        "change_pct": {
            "1_bar": _change(c, 1),
            "5_bar": _change(c, 5),
            "21_bar": _change(c, 21),
            "63_bar": _change(c, 63),
            "252_bar": _change(c, 252),
        },
        "range_52w": {
            "high": number(np.nanmax(window)),
            "low": number(np.nanmin(window)),
            "from_high_pct": number((last / np.nanmax(window) - 1) * 100, 2),
        },
        "moving_averages": {
            "sma20": _last(sma20),
            "sma50": _last(sma50),
            "sma200": _last(sma200),
            "ema20": _last(ta.ema(c, 20)),
        },
        "rsi14": _last(rsi, 1),
        "macd": {
            "macd": _last(macd, 3),
            "signal": _last(signal, 3),
            "hist": _last(hist, 3),
        },
        "bollinger": {
            "upper": _last(upper),
            "middle": _last(basis),
            "lower": _last(lower),
            "percent_b": number((last - lower[-1]) / width, 2) if width else None,
        },
        "atr14": _last(atr),
        "atr_pct": number(atr[-1] / last * 100, 2),
        "adx14": {
            "adx": _last(adx, 1),
            "plus_di": _last(plus, 1),
            "minus_di": _last(minus, 1),
        },
        "stochastic": {"k": _last(k, 1), "d": _last(d, 1)},
        "supertrend": {
            "line": _last(st_line),
            "direction": "yukarı" if st_dir[-1] < 0 else "aşağı",
        },
        "volume_vs_20d": volume_ratio,
        "obv_change_20_bar_pct": number(
            (obv[-1] - obv[-21]) / abs(obv[-21]) * 100
            if len(obv) > 21 and obv[-21]
            else None,
            1,
        ),
        "pivot_resistance": resistance,
        "pivot_support": support,
        "signals": signals,
    }


# --- Strategies and scripts --------------------------------------------------------


def strategy_catalog() -> dict[str, dict[str, Any]]:
    """Built-in strategies and strategy scripts with their parameters."""
    catalog = {
        name: {
            "label": cls.__name__,
            "kind": "builtin",
            "doc": (cls.__doc__ or "").strip(),
            "params": strategy_params(cls),
            "grid": getattr(cls, "param_grid", {}),
        }
        for name, cls in STRATEGIES.items()
    }
    for entry in list_scripts():
        if entry["kind"] != "strategy" or entry["error"]:
            continue
        cls = script_strategy(load_script(entry["name"]))
        catalog[f"script:{entry['name']}"] = {
            "label": entry["title"],
            "kind": "script",
            "doc": entry["description"],
            "params": strategy_params(cls),
            "grid": cls.param_grid,
            "inputs": entry["inputs"],
        }
    return catalog


def resolve_strategy(name: str | None, source: str | None = None) -> type[Strategy]:
    """Return a strategy class from a name or from script source code."""
    if source:
        return script_strategy(source, "editör")
    if not name:
        raise ValueError("Bir strateji adı ya da script kaynağı verin.")
    try:
        return get_strategy(name)
    except ValueError:
        raise ValueError(
            f"Bilinmeyen strateji: {name}. Hazır stratejiler: {', '.join(STRATEGIES)};"
            " scriptler 'script:<ad>' biçiminde."
        ) from None


def _grid_size(grid: dict[str, list]) -> int:
    size = 1
    for values in grid.values():
        size *= max(len(values), 1)
    return size


def backtest_series(report: BacktestReport) -> dict[str, Any]:
    """Equity, buy-and-hold and trades of a backtest, for charting."""
    equity = report.equity_curve["Equity"]
    points = [{"t": stamp(t), "v": round(float(v), 2)} for t, v in equity.items()]
    hold = []
    if report.data is not None:
        close = report.data["Close"].reindex(equity.index)
        start = float(equity.iloc[0])
        hold = [
            {"t": stamp(t), "v": round(start * float(c) / float(close.iloc[0]), 2)}
            for t, c in close.items()
        ]
    trades = [
        {
            "entry_time": stamp(row.EntryTime),
            "exit_time": stamp(row.ExitTime),
            "size": int(row.Size),
            "entry_price": round(float(row.EntryPrice), 4),
            "exit_price": round(float(row.ExitPrice), 4),
            "pnl": round(float(row.PnL), 2),
            "return_pct": round(float(row.ReturnPct) * 100, 4),
        }
        for row in report.trades.itertuples()
    ]
    return {"equity": thin(points), "hold": thin(hold), "trade_list": trades}


def backtest(
    symbol: str,
    strategy: str | None = "sma_cross",
    *,
    source: str | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
    interval: str = "1d",
    params: dict[str, Any] | None = None,
    optimize: bool = False,
    param_grid: dict[str, list] | None = None,
    maximize: str = "Sharpe Ratio",
    holdout: float | None = 0.3,
    cash: float = 100_000,
    costs: BistCosts | None = None,
    slippage: float = 0.001,
    benchmark: bool = True,
) -> tuple[dict[str, Any], BacktestReport]:
    """Run or optimize a backtest; return the JSON body and the shown report."""
    strategy_cls = resolve_strategy(strategy, source)
    common = {
        "start": _parse_date(start) or today() - timedelta(days=365 * 2),
        "end": _parse_date(end),
        "interval": interval,
        "cash": cash,
        "costs": costs,
        "slippage": slippage,
        "benchmark": "XU100" if benchmark else None,
        "usd": benchmark,
    }
    code = normalize_symbol(symbol)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if optimize:
            grid = param_grid or getattr(strategy_cls, "param_grid", None)
            if not grid:
                raise ValueError("Bu stratejinin optimize edilecek parametresi yok.")
            size = _grid_size(grid)
            result = optimize_backtest(
                code,
                strategy_cls,
                param_grid=grid,
                maximize=maximize,
                holdout=holdout or None,
                max_tries=MAX_COMBINATIONS if size > MAX_COMBINATIONS else None,
                random_state=0,
                **common,
            )
            report = result.out_of_sample or result.in_sample
            body = {**result.summary(), "combinations": min(size, MAX_COMBINATIONS)}
        else:
            report = run_backtest(
                code, strategy_cls, params=whole(params or {}), **common
            )
            body = report.summary()
    body["warnings"] = sorted({str(w.message) for w in caught})[:5]
    if source:
        body["strategy"] = "editör scripti"
    return body, report


def walkforward(
    symbol: str,
    strategy: str | None = "sma_cross",
    *,
    source: str | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
    train: int = 504,
    test: int = 126,
    param_grid: dict[str, list] | None = None,
    cash: float = 100_000,
    costs: BistCosts | None = None,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """Run a walk-forward test and return its summary with an equity curve."""
    strategy_cls = resolve_strategy(strategy, source)
    grid = param_grid or getattr(strategy_cls, "param_grid", None)
    if grid and _grid_size(grid) > MAX_COMBINATIONS:
        raise ValueError(
            f"Parametre ızgarası çok büyük ({_grid_size(grid)} kombinasyon);"
            f" walk-forward için en fazla {MAX_COMBINATIONS}. Daha az değer verin."
        )
    report = walk_forward(
        normalize_symbol(symbol),
        strategy_cls,
        start=_parse_date(start) or today() - timedelta(days=365 * 5),
        end=_parse_date(end),
        train_bars=train,
        test_bars=test,
        param_grid=grid,
        cash=cash,
        costs=costs,
        slippage=slippage,
    )
    curve = [
        {"t": point["time"][:10], "v": round(point["equity"], 2)}
        for point in report.equity_curve
    ]
    body = report.summary()
    # "equity" is the curve for charts; the final amount moves to equity_final.
    return {**body, "equity_final": body["equity"], "equity": thin(curve)}


def _script(name: str | None, source: str | None):
    if source:
        return compile_script(source, name or "editör")
    if not name:
        raise ValueError("Bir script adı ya da kaynak kodu verin.")
    try:
        return load_script(name.removeprefix("script:"))
    except KeyError:
        raise ValueError(f"Script bulunamadı: {name}") from None


def _series_values(values: np.ndarray, digits: int = 4) -> list[float | None]:
    return [None if not math.isfinite(x) else round(float(x), digits) for x in values]


def _transitions(
    entries: np.ndarray | None, exits: np.ndarray | None
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Keep only the signals that change the position, as a chart shows trades.

    A strategy usually repeats its signal on every bar the condition holds; the
    first entry while flat and the first exit while long are the ones that act.
    """
    if entries is None or exits is None:
        return entries, exits
    first_entries = np.zeros_like(entries)
    first_exits = np.zeros_like(exits)
    long = False
    for i in range(len(entries)):
        if long and exits[i]:
            first_exits[i] = True
            long = False
        elif not long and entries[i]:
            first_entries[i] = True
            long = True
    return first_entries, first_exits


def script_payload(script, result, frame: pd.DataFrame, shown) -> dict[str, Any]:
    """Return a script's output on the shown bars, for drawing on a chart."""
    start = 0
    if shown is not None:
        start = int(np.searchsorted(pd.DatetimeIndex(frame.index), shown))
    view = frame.iloc[start:]
    times = [stamp(t) for t in view.index]

    def marks(mask: np.ndarray | None) -> list[str]:
        if mask is None:
            return []
        return [times[i] for i in np.flatnonzero(mask[start:])]

    entries, exits = _transitions(result.entries, result.exits)

    return {
        "script": script.describe(),
        "inputs": result.inputs,
        "rows": candles(view),
        "plots": [
            {
                "title": plot.title,
                "values": _series_values(plot.values[start:]),
                "color": plot.color,
                "colors": plot.colors[start:] if plot.colors else None,
                "style": plot.style,
                "linewidth": plot.linewidth,
                "overlay": plot.overlay,
            }
            for plot in result.plots
        ],
        "shapes": [
            {
                "title": shape.title,
                "times": marks(shape.mask),
                "style": shape.style,
                "location": shape.location,
                "color": shape.color,
                "text": shape.text,
            }
            for shape in result.shapes
        ],
        "hlines": [asdict(line) for line in result.hlines],
        "alerts": [
            {
                "title": alert.title,
                "message": alert.message,
                "times": marks(alert.mask)[-20:],
            }
            for alert in result.alerts
        ],
        "entries": marks(entries),
        "exits": marks(exits),
        "warnings": result.warnings,
    }


def run_script(
    symbol: str,
    *,
    name: str | None = None,
    source: str | None = None,
    inputs: dict[str, Any] | None = None,
    span: str | None = "1Y",
    start: date | str | None = None,
    end: date | str | None = None,
    interval: str | None = None,
) -> dict[str, Any]:
    """Run a script on a symbol and return what it draws and signals."""
    script = _script(name, source)
    if span and not start:
        interval, start, end = span_window(span)
    interval = interval or "1d"
    code = normalize_symbol(symbol)
    frame, shown = load_bars(code, start, end, interval, warmup=True)
    result = script.run(frame, inputs, symbol=code, interval=interval)
    payload = script_payload(script, result, frame, shown)
    payload.update(symbol=code, interval=interval, name=BIST_INDICES.get(code))
    return payload


def script_summary(payload: dict[str, Any], bars: int = 5) -> dict[str, Any]:
    """Shrink a run_script payload to the last values, for the AI."""
    rows = payload["rows"]
    last_time = rows[-1]["t"] if rows else None
    return {
        "symbol": payload["symbol"],
        "script": {key: payload["script"][key] for key in ("title", "kind", "overlay")},
        "inputs": payload["inputs"],
        "last_bar": rows[-1] if rows else None,
        "plots": {
            plot["title"]: [v for v in plot["values"][-bars:]]
            for plot in payload["plots"]
        },
        "hlines": [line["price"] for line in payload["hlines"]],
        "entries_recent": payload["entries"][-5:],
        "exits_recent": payload["exits"][-5:],
        "signal_on_last_bar": {
            "entry": bool(payload["entries"]) and payload["entries"][-1] == last_time,
            "exit": bool(payload["exits"]) and payload["exits"][-1] == last_time,
        },
        "alerts": [
            {"title": a["title"], "last": a["times"][-1] if a["times"] else None}
            for a in payload["alerts"]
        ],
        "shapes": [
            {"title": s["title"], "last": s["times"][-1] if s["times"] else None}
            for s in payload["shapes"]
        ],
        "warnings": payload["warnings"],
    }


def _sample_frame(bars: int = 300) -> pd.DataFrame:
    """Deterministic made-up daily bars to dry-run scripts on."""
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, bars)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.003, bars))
    index = pd.bdate_range("2023-01-02", periods=bars, name="Date")
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) * 1.01,
            "Low": np.minimum(open_, close) * 0.99,
            "Close": close,
            "Volume": rng.integers(100_000, 1_000_000, bars).astype(float),
        },
        index=index,
    )


def check_script(source: str) -> dict[str, Any]:
    """Compile a script and dry-run it on sample bars to find errors early.

    Returns the declaration and inputs, or the first error with its line.
    """
    try:
        script = compile_script(source, "editör")
        result = script.run(_sample_frame(), symbol="ORNEK", interval="1d")
    except ScriptError as error:
        return {"ok": False, "error": error.to_dict()}
    return {
        "ok": True,
        "error": None,
        **script.describe(),
        "warnings": result.warnings,
        "plots": [plot.title for plot in result.plots],
    }


def screen(
    symbols: list[str] | None,
    *,
    name: str | None = None,
    source: str | None = None,
    inputs: dict[str, Any] | None = None,
    interval: str = "1d",
) -> dict[str, Any]:
    """Run a script on many symbols and report each one's last-bar state."""
    script = _script(name, source)
    codes = list(dict.fromkeys(normalize_symbol(s) for s in (symbols or WATCHLIST)))
    codes = codes[:MAX_SCREEN]
    end = today()
    span = {"1d": 400, "1W": 2000}.get(interval, 40)

    def one(code: str) -> dict[str, Any]:
        try:
            frame, _ = load_bars(code, end - timedelta(days=span), end, interval)
            result = script.run(frame, inputs, symbol=code, interval=interval)
        except Exception as error:
            return {"symbol": code, "error": str(error)}
        close = frame["Close"]
        row: dict[str, Any] = {
            "symbol": code,
            "date": stamp(frame.index[-1]),
            "close": number(close.iloc[-1]),
            "change_pct": number((close.iloc[-1] / close.iloc[-2] - 1) * 100, 2)
            if len(close) > 1
            else None,
            "plots": {p.title: number(p.values[-1], 3) for p in result.plots},
            "alerts": [a.title for a in result.alerts if len(a.mask) and a.mask[-1]],
            "shapes": [s.title for s in result.shapes if len(s.mask) and s.mask[-1]],
        }
        if result.entries is not None:
            entries, exits = _transitions(result.entries, result.exits)
            last_entry = np.flatnonzero(entries)
            last_exit = np.flatnonzero(exits)
            row["entry_signal"] = bool(entries[-1])
            row["exit_signal"] = bool(exits[-1])
            row["in_position"] = bool(
                len(last_entry)
                and (not len(last_exit) or last_entry[-1] > last_exit[-1])
            )
        return row

    with ThreadPoolExecutor(max_workers=min(6, max(len(codes), 1))) as pool:
        rows = list(pool.map(one, codes))
    return {"script": script.describe()["title"], "interval": interval, "results": rows}


def grid_combinations(grid: dict[str, list]) -> list[dict[str, Any]]:
    """Expand a parameter grid (used to preview optimization size)."""
    names = list(grid)
    return [dict(zip(names, values)) for values in product(*grid.values())]


# --- Paper account ------------------------------------------------------------------


def paper_status(path: Path, *, detail: bool = True) -> dict[str, Any]:
    """Return the paper account's state, or ``{"exists": False}``."""
    if not path.exists():
        return {"exists": False}
    with PaperAccount(path) as account:
        body: dict[str, Any] = {
            "exists": True,
            **account.summary(),
            "buying_power": round(account.buying_power(), 2),
            "created_at": account.created_at.isoformat(),
        }
        if detail:
            curve = [
                {"t": point["time"], "v": round(point["equity"], 2)}
                for point in account.equity_curve()
            ]
            body.update(
                settings={
                    **asdict(account.costs),
                    "slippage": account.slippage,
                    "dividend_tax": account.dividend_tax,
                },
                equity_curve=thin(curve),
                orders=[o.to_dict() for o in account.orders()][-50:],
                fills=[f.to_dict() for f in account.fills()][-50:],
            )
        else:
            body["recent_fills"] = [f.to_dict() for f in account.fills()][-10:]
        return body
