"""Signal research: how stocks behave and which signals preceded their moves.

``study`` reads only the bars up to a cutoff date, so a strategy designed from
its findings can be tested blind on the bars after the cutoff. Everything comes
from OHLCV bars: Yahoo has no order book, so market depth is estimated from the
traded value (liquidity) instead.

The same indicator bundle feeds the AI's decisions during a blind backtest
(``indicators`` and ``active_signals`` on a frame cut at the decision bar).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer.paper.account import normalize_symbol
from marketalyzer.scripting import ta
from marketalyzer.services import _parse_date, load_bars, number, stamp, today
from openbb_bist.utils.constants import BIST_INDICES

HORIZONS = (5, 10, 20)
MAIN_HORIZON = 10
MAX_SYMBOLS = 8
MIN_EVENTS = 8
MIN_BARS = 120
STUDY_INTERVALS = ("1d", "1W", "1h")
# BIST has nine hourly bars a day (09:30 to 17:30 and the 18:00 close).
BARS_PER_YEAR = {"1d": 252, "1W": 52, "1h": 252 * 9}
BENCHMARK = "XU100"
WEEKDAYS = ("Pzt", "Sal", "Çar", "Per", "Cum")
DEPTH_NOTE = (
    "Yahoo emir defteri (derinlik/kademe) verisi sunmaz; derinlik yerine günlük TL"
    " hacmi, sıfır hacimli gün oranı ve Amihud likidite ölçüsü kullanıldı."
)
# Median daily traded value (TL) -> liquidity bucket, used when names are hidden.
LIQUIDITY_BUCKETS = ((5e9, "çok yüksek"), (1e9, "yüksek"), (1e8, "orta"), (0, "düşük"))


# --- Indicators -------------------------------------------------------------------


def indicators(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """Compute every series the signals and the AI snapshots read.

    All of them are causal: a value on a bar uses only that bar and earlier ones.
    """
    o, h, low, c = (
        frame[col].to_numpy(dtype=float) for col in ("Open", "High", "Low", "Close")
    )
    v = frame["Volume"].to_numpy(dtype=float) if "Volume" in frame else np.zeros(len(c))
    v = np.nan_to_num(v)
    hlc3 = (h + low + c) / 3
    macd, signal, hist = ta.macd(c, 12, 26, 9)
    mid, upper, lower = ta.bb(c, 20, 2)
    plus, minus, adx = ta.dmi(h, low, c, 14, 14)
    k = ta.sma(ta.stoch(c, h, low, 14), 3)
    _, direction = ta.supertrend(h, low, c, 3, 10)
    obv = ta.obv(c, v)
    return {
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": v,
        "sma20": ta.sma(c, 20),
        "sma50": ta.sma(c, 50),
        "sma200": ta.sma(c, 200),
        "ema12": ta.ema(c, 12),
        "ema26": ta.ema(c, 26),
        "rsi": ta.rsi(c, 14),
        "macd": macd,
        "macd_signal": signal,
        "macd_hist": hist,
        "bb_mid": mid,
        "bb_upper": upper,
        "bb_lower": lower,
        "atr": ta.atr(h, low, c, 14),
        "plus_di": plus,
        "minus_di": minus,
        "adx": adx,
        "stoch_k": k,
        "stoch_d": ta.sma(k, 3),
        "cci": ta.cci(hlc3, 20),
        "mfi": ta.mfi(hlc3, v, 14),
        "st_dir": direction,
        "obv": obv,
        # Levels of the previous bars, so a close above them is a breakout.
        "high20": ta.shift(ta.highest(h, 20)),
        "low20": ta.shift(ta.lowest(low, 20)),
        "close_high252": ta.shift(ta.highest(c, 252)),
        "close_low252": ta.shift(ta.lowest(c, 252)),
        "obv_high20": ta.shift(ta.highest(obv, 20)),
        "volume20": ta.shift(ta.sma(v, 20)),
    }


# --- Signals ----------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    """A candidate signal: a rule that marks bars, with its expected direction."""

    key: str
    label: str
    group: str
    bias: int  # 1: expected to precede a rise, -1: a fall
    rule: Callable[[dict[str, np.ndarray]], np.ndarray]

    def describe(self) -> dict[str, Any]:
        """Return the signal's metadata for the UI and the AI."""
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "bias": "yükseliş" if self.bias > 0 else "düşüş",
        }


def _edge(mask: np.ndarray) -> np.ndarray:
    """Keep the first bar of each run, so a lasting condition counts once."""
    mask = np.asarray(mask, dtype=bool)
    return mask & ~ta.shift(mask)


def _prev(x: np.ndarray, bars: int = 1) -> np.ndarray:
    return ta.shift(x, bars)


def _level(ind: dict[str, np.ndarray], value: float) -> np.ndarray:
    """Return a constant series, to compare an indicator with a fixed level."""
    return np.full(len(ind["close"]), float(value))


def _flip(direction: np.ndarray, to: float) -> np.ndarray:
    return (direction == to) & (_prev(direction) == -to)


SIGNALS: tuple[Signal, ...] = (
    # Trend
    Signal("golden_cross", "SMA50, SMA200'ü yukarı kesti (golden cross)", "trend", 1,
           lambda i: ta.crossover(i["sma50"], i["sma200"])),
    Signal("death_cross", "SMA50, SMA200'ü aşağı kesti (death cross)", "trend", -1,
           lambda i: ta.crossunder(i["sma50"], i["sma200"])),
    Signal("sma20_50_up", "SMA20, SMA50'yi yukarı kesti", "trend", 1,
           lambda i: ta.crossover(i["sma20"], i["sma50"])),
    Signal("sma20_50_down", "SMA20, SMA50'yi aşağı kesti", "trend", -1,
           lambda i: ta.crossunder(i["sma20"], i["sma50"])),
    Signal("above_sma200", "Fiyat SMA200 üstüne çıktı", "trend", 1,
           lambda i: ta.crossover(i["close"], i["sma200"])),
    Signal("below_sma200", "Fiyat SMA200 altına indi", "trend", -1,
           lambda i: ta.crossunder(i["close"], i["sma200"])),
    Signal("supertrend_up", "Supertrend yükselişe döndü", "trend", 1,
           lambda i: _flip(i["st_dir"], -1.0)),
    Signal("supertrend_down", "Supertrend düşüşe döndü", "trend", -1,
           lambda i: _flip(i["st_dir"], 1.0)),
    Signal("adx_trend_up", "ADX 25'i aştı, +DI önde (güçlenen yükseliş)", "trend", 1,
           lambda i: ta.crossover(i["adx"], _level(i, 25)) & (i["plus_di"] > i["minus_di"])),
    Signal("adx_trend_down", "ADX 25'i aştı, -DI önde (güçlenen düşüş)", "trend", -1,
           lambda i: ta.crossover(i["adx"], _level(i, 25)) & (i["plus_di"] < i["minus_di"])),
    Signal("donchian_up", "20 barın zirvesi kırıldı", "kırılım", 1,
           lambda i: _edge(i["close"] > i["high20"])),
    Signal("donchian_down", "20 barın dibi kırıldı", "kırılım", -1,
           lambda i: _edge(i["close"] < i["low20"])),
    Signal("high_52w", "52 haftalık kapanış zirvesi", "kırılım", 1,
           lambda i: _edge(i["close"] > i["close_high252"])),
    Signal("low_52w", "52 haftalık kapanış dibi", "kırılım", -1,
           lambda i: _edge(i["close"] < i["close_low252"])),
    # Momentum
    Signal("macd_up", "MACD sinyal çizgisini yukarı kesti", "momentum", 1,
           lambda i: ta.crossover(i["macd"], i["macd_signal"])),
    Signal("macd_down", "MACD sinyal çizgisini aşağı kesti", "momentum", -1,
           lambda i: ta.crossunder(i["macd"], i["macd_signal"])),
    Signal("macd_zero_up", "MACD sıfırın üstüne çıktı", "momentum", 1,
           lambda i: ta.crossover(i["macd"], _level(i, 0.0))),
    Signal("macd_zero_down", "MACD sıfırın altına indi", "momentum", -1,
           lambda i: ta.crossunder(i["macd"], _level(i, 0.0))),
    Signal("rsi_50_up", "RSI 50'yi yukarı kesti", "momentum", 1,
           lambda i: ta.crossover(i["rsi"], _level(i, 50))),
    Signal("rsi_50_down", "RSI 50'yi aşağı kesti", "momentum", -1,
           lambda i: ta.crossunder(i["rsi"], _level(i, 50))),
    # Mean reversion
    Signal("rsi_30_up", "RSI aşırı satımdan çıktı (30 yukarı)", "dönüş", 1,
           lambda i: ta.crossover(i["rsi"], _level(i, 30))),
    Signal("rsi_70_down", "RSI aşırı alımdan döndü (70 aşağı)", "dönüş", -1,
           lambda i: ta.crossunder(i["rsi"], _level(i, 70))),
    Signal("stoch_low_cross", "Stokastik 20 altında yukarı kesişti", "dönüş", 1,
           lambda i: ta.crossover(i["stoch_k"], i["stoch_d"]) & (i["stoch_k"] < 20)),
    Signal("stoch_high_cross", "Stokastik 80 üstünde aşağı kesişti", "dönüş", -1,
           lambda i: ta.crossunder(i["stoch_k"], i["stoch_d"]) & (i["stoch_k"] > 80)),
    Signal("bb_lower_back", "Fiyat alt Bollinger bandına geri girdi", "dönüş", 1,
           lambda i: ta.crossover(i["close"], i["bb_lower"])),
    Signal("bb_upper_back", "Fiyat üst Bollinger bandından içeri döndü", "dönüş", -1,
           lambda i: ta.crossunder(i["close"], i["bb_upper"])),
    Signal("cci_up", "CCI -100'ü yukarı kesti", "dönüş", 1,
           lambda i: ta.crossover(i["cci"], _level(i, -100))),
    Signal("mfi_up", "MFI 20'yi yukarı kesti (para girişi)", "dönüş", 1,
           lambda i: ta.crossover(i["mfi"], _level(i, 20))),
    Signal("three_down", "Üç bar üst üste düşüş", "dönüş", 1,
           lambda i: _edge((i["close"] < _prev(i["close"]))
                           & (_prev(i["close"]) < _prev(i["close"], 2))
                           & (_prev(i["close"], 2) < _prev(i["close"], 3)))),
    Signal("trend_pullback", "Yükseliş trendinde geri çekilme (RSI 40 altı)", "dönüş", 1,
           lambda i: _edge((i["rsi"] < 40) & (i["sma50"] > i["sma200"])
                           & (i["close"] > i["sma200"]))),
    # Volume
    Signal("volume_up", "Yüksek hacimli yükseliş (hacim > 2 x ortalama)", "hacim", 1,
           lambda i: _edge((i["volume"] > 2 * i["volume20"])
                           & (i["close"] > _prev(i["close"])))),
    Signal("volume_down", "Yüksek hacimli düşüş (hacim > 2 x ortalama)", "hacim", -1,
           lambda i: _edge((i["volume"] > 2 * i["volume20"])
                           & (i["close"] < _prev(i["close"])))),
    Signal("obv_breakout", "OBV 20 barın zirvesinde (birikim)", "hacim", 1,
           lambda i: _edge(i["obv"] > i["obv_high20"])),
    # Price action
    Signal("gap_up", "%2'den büyük boşlukla yukarı açılış", "fiyat", 1,
           lambda i: i["open"] > _prev(i["close"]) * 1.02),
    Signal("gap_down", "%2'den büyük boşlukla aşağı açılış", "fiyat", -1,
           lambda i: i["open"] < _prev(i["close"]) * 0.98),
    Signal("inside_break", "İç bar sonrası yukarı kırılım", "fiyat", 1,
           lambda i: (_prev(i["high"]) < _prev(i["high"], 2))
           & (_prev(i["low"]) > _prev(i["low"], 2))
           & (i["close"] > _prev(i["high"]))),
)  # fmt: skip
SIGNAL_BY_KEY = {signal.key: signal for signal in SIGNALS}


def signal_masks(ind: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Evaluate every catalog signal on an indicator bundle."""
    with np.errstate(invalid="ignore"):
        return {s.key: np.asarray(s.rule(ind), dtype=bool) for s in SIGNALS}


def active_signals(ind: dict[str, np.ndarray], bar: int = -1) -> list[str]:
    """Return the catalog signals that fire on a bar (the last one by default)."""
    return [key for key, mask in signal_masks(ind).items() if len(mask) and mask[bar]]


# --- Statistics -------------------------------------------------------------------


def _pct(value: float, digits: int = 2) -> float | None:
    return number(value * 100, digits) if value is not None else None


def forward_returns(ind: dict[str, np.ndarray], horizon: int) -> np.ndarray:
    """Return from the next bar's open to the close ``horizon`` bars later.

    A signal is known at a bar's close, so a trade can only start at the next
    open. Bars without ``horizon`` later bars are NaN.
    """
    o, c = ind["open"], ind["close"]
    n = len(c)
    out = np.full(n, np.nan)
    if n > horizon:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[: n - horizon] = c[horizon:] / o[1 : n - horizon + 1] - 1
    out[~np.isfinite(out)] = np.nan
    return out


def _strength(n: int, t_stat: float | None) -> str:
    if n < MIN_EVENTS or t_stat is None:
        return "az örnek"
    if abs(t_stat) >= 2:
        return "güçlü"
    if abs(t_stat) >= 1:
        return "zayıf"
    return "etkisiz"


def signal_stats(
    ind: dict[str, np.ndarray],
    first: int = 0,
    masks: dict[str, np.ndarray] | None = None,
) -> list[dict[str, Any]]:
    """Forward returns after each signal on bars ``first`` onwards.

    ``excess`` compares the average return after the signal with the average
    over every bar of the same window, so a rising market does not make every
    bullish signal look good. ``t`` is a rough t-statistic of the 10-bar excess
    return (events can overlap, so it overstates certainty).
    """
    masks = masks if masks is not None else signal_masks(ind)
    forward = {h: forward_returns(ind, h) for h in HORIZONS}
    baseline = {h: np.nanmean(forward[h][first:]) for h in HORIZONS}
    n_bars = len(ind["close"])
    rows = []
    for signal in SIGNALS:
        mask = masks[signal.key].copy()
        mask[:first] = False
        bars = np.flatnonzero(mask)
        row: dict[str, Any] = {**signal.describe(), "events": int(len(bars))}
        for h in HORIZONS:
            values = forward[h][bars]
            values = values[np.isfinite(values)]
            if len(values):
                mean = float(values.mean())
                row[f"h{h}"] = {
                    "mean_pct": _pct(mean),
                    "hit_pct": number((values > 0).mean() * 100, 1),
                    "excess_pct": _pct(mean - baseline[h])
                    if np.isfinite(baseline[h])
                    else None,
                }
            else:
                row[f"h{h}"] = {"mean_pct": None, "hit_pct": None, "excess_pct": None}
        values = forward[MAIN_HORIZON][bars]
        values = values[np.isfinite(values)]
        t_stat = None
        if len(values) >= 2 and np.isfinite(baseline[MAIN_HORIZON]):
            spread = values.std(ddof=1)
            if spread > 0:
                t_stat = float(
                    (values.mean() - baseline[MAIN_HORIZON])
                    / (spread / math.sqrt(len(values)))
                )
        row["t"] = number(t_stat, 2) if t_stat is not None else None
        row["strength"] = _strength(len(values), t_stat)
        row["active_now"] = bool(len(mask) and mask[-1])
        row["bars_since"] = int(n_bars - 1 - bars[-1]) if len(bars) else None
        rows.append(row)
    return rows


def _max_drawdown(close: np.ndarray) -> float:
    peaks = np.maximum.accumulate(close)
    return float((close / peaks - 1).min()) if len(close) else 0.0


def liquidity_bucket(value: float | None) -> str:
    """Name the liquidity bucket of a median daily traded value in TL."""
    for floor, label in LIQUIDITY_BUCKETS:
        if value is not None and value >= floor:
            return label
    return "bilinmiyor"


def behavior(
    ind: dict[str, np.ndarray],
    index: pd.Index,
    first: int = 0,
    benchmark: pd.Series | None = None,
    interval: str = "1d",
) -> dict[str, Any]:
    """How a stock moved over bars ``first`` onwards: trend, risk, liquidity."""
    c = ind["close"][first:]
    o, h, low, v = (ind[key][first:] for key in ("open", "high", "low", "volume"))
    prev = np.r_[np.nan, c[:-1]]
    returns = c / prev - 1
    r = returns[1:]
    r = r[np.isfinite(r)]
    per_year = BARS_PER_YEAR.get(interval, 252)
    traded = c * v
    with np.errstate(divide="ignore", invalid="ignore"):
        illiquidity = np.abs(returns) / traded
    illiquidity = illiquidity[np.isfinite(illiquidity) & (traded > 0)]
    sma200 = ind["sma200"][first:]
    trend_known = np.isfinite(sma200)
    autocorr = (
        float(np.corrcoef(r[1:], r[:-1])[0, 1]) if len(r) > 10 and r.std() > 0 else None
    )
    if autocorr is None:
        tendency = "bilinmiyor"
    elif autocorr > 0.05:
        tendency = "momentum (hareketler devam etme eğiliminde)"
    elif autocorr < -0.05:
        tendency = "ortalamaya dönüş (hareketler geri alınma eğiliminde)"
    else:
        tendency = "belirgin eğilim yok (rastgele yürüyüşe yakın)"
    median_traded = float(np.median(traded)) if len(traded) else None
    result: dict[str, Any] = {
        "bars": int(len(c)),
        "return_pct": _pct(c[-1] / c[0] - 1) if len(c) > 1 else None,
        "volatility_ann_pct": number(r.std() * math.sqrt(per_year) * 100, 1)
        if len(r) > 1
        else None,
        "avg_move_pct": _pct(float(np.abs(r).mean())) if len(r) else None,
        "best_bar_pct": _pct(float(r.max())) if len(r) else None,
        "worst_bar_pct": _pct(float(r.min())) if len(r) else None,
        "up_bars_pct": number((r > 0).mean() * 100, 1) if len(r) else None,
        "max_drawdown_pct": _pct(_max_drawdown(c)),
        "above_sma200_pct": number(
            (c[trend_known] > sma200[trend_known]).mean() * 100, 1
        )
        if trend_known.any()
        else None,
        "adx_median": number(np.nanmedian(ind["adx"][first:]), 1)
        if np.isfinite(ind["adx"][first:]).any()
        else None,
        "autocorr_1": number(autocorr, 3) if autocorr is not None else None,
        "tendency": tendency,
        "gap_avg_pct": _pct(float(np.nanmean(np.abs(o[1:] / c[:-1] - 1))), 2)
        if len(c) > 1
        else None,
        "range_avg_pct": _pct(float(np.nanmean((h - low) / c)), 2) if len(c) else None,
        "liquidity": {
            "median_traded_tl": number(median_traded, 0),
            "bucket": liquidity_bucket(median_traded),
            "amihud": number(float(illiquidity.mean()) * 1e9, 4)
            if len(illiquidity)
            else None,
            "zero_volume_pct": number((v <= 0).mean() * 100, 1) if len(v) else None,
        },
    }
    if interval == "1d" and len(c) > 20:
        days = pd.DatetimeIndex(index[first:]).dayofweek
        result["weekday_mean_pct"] = {
            name: _pct(float(np.nanmean(returns[days == d])), 3)
            if (days == d).sum() > 1
            else None
            for d, name in enumerate(WEEKDAYS)
        }
    if benchmark is not None and len(c) > 20:
        stock = pd.Series(returns, index=index[first:])
        joined = pd.concat([stock, benchmark], axis=1, join="inner").dropna()
        if len(joined) > 20 and joined.iloc[:, 1].var() > 0:
            cov = joined.cov().iloc[0, 1]
            result["beta"] = number(cov / joined.iloc[:, 1].var(), 2)
            result["benchmark_corr"] = number(joined.corr().iloc[0, 1], 2)
            growth = (1 + joined).prod() - 1
            result["vs_benchmark_pct"] = _pct(float(growth.iloc[0] - growth.iloc[1]))
    return result


def _benchmark_returns(
    frame: pd.DataFrame | None, start: pd.Timestamp
) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    close = frame["Close"]
    returns = close / close.shift() - 1
    return returns[returns.index >= start]


# --- Study ------------------------------------------------------------------------


def _codes(symbols: list[str]) -> list[str]:
    codes = list(
        dict.fromkeys(normalize_symbol(s) for s in symbols if s and str(s).strip())
    )
    if not codes:
        raise ValueError("En az bir hisse seçin.")
    if len(codes) > MAX_SYMBOLS:
        raise ValueError(f"En fazla {MAX_SYMBOLS} hisse seçilebilir.")
    return codes


def study_window(cutoff: date | str | None, years: float) -> tuple[date, date]:
    """Return the first and last day of the study window ending at ``cutoff``."""
    end = _parse_date(cutoff) or today()
    if end > today():
        raise ValueError("Kesim tarihi bugünden sonra olamaz.")
    if not 0.25 <= years <= 10:
        raise ValueError("Eğitim süresi 3 ay ile 10 yıl arasında olmalı.")
    return end - timedelta(days=round(years * 365.25)), end


def correlation(returns: dict[str, pd.Series]) -> dict[str, Any] | None:
    """Pairwise correlation of bar returns on the dates all symbols share."""
    if len(returns) < 2:
        return None
    joined = pd.concat(returns, axis=1, join="inner").dropna()
    if len(joined) < 20:
        return None
    matrix = joined.corr().to_numpy()
    names = list(returns)
    upper = matrix[np.triu_indices(len(names), 1)]
    return {
        "symbols": names,
        "matrix": [[number(x, 2) for x in row] for row in matrix],
        "average": number(float(np.nanmean(upper)), 2),
        "bars": int(len(joined)),
    }


def rank_signals(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Combine each signal's results over all symbols, strongest first.

    The score is the event-weighted 10-bar excess return, signed so that a
    signal that did what its bias says scores positive.
    """
    ranking = []
    for signal in SIGNALS:
        rows = [
            next(r for r in rows if r["key"] == signal.key)
            for rows in rows_by_symbol.values()
        ]
        weighted = 0.0
        events = 0
        agree = 0
        for row in rows:
            excess = row[f"h{MAIN_HORIZON}"]["excess_pct"]
            if excess is None or not row["events"]:
                continue
            weighted += excess * row["events"]
            events += row["events"]
            if row["strength"] in ("güçlü", "zayıf") and excess * signal.bias > 0:
                agree += 1
        average = weighted / events if events else None
        ranking.append(
            {
                **signal.describe(),
                "events": events,
                "excess_pct": number(average, 2) if average is not None else None,
                "score": number(average * signal.bias, 2)
                if average is not None
                else None,
                "agreeing_symbols": agree,
                "active_now": [
                    symbol
                    for symbol, rows in rows_by_symbol.items()
                    if next(r for r in rows if r["key"] == signal.key)["active_now"]
                ],
            }
        )
    ranking.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return ranking


def study_frames(
    frames: dict[str, tuple[pd.DataFrame, pd.Timestamp | None]],
    benchmark: pd.DataFrame | None = None,
    interval: str = "1d",
) -> dict[str, Any]:
    """Study already loaded frames; each comes with the first bar to study."""
    symbols = []
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    returns: dict[str, pd.Series] = {}
    for code, (frame, shown) in frames.items():
        if len(frame) < MIN_BARS:
            raise ValueError(
                f"{code}: araştırma için en az {MIN_BARS} bar gerekli"
                f" ({len(frame)} bar var). Eğitim süresini uzatın."
            )
        index = pd.DatetimeIndex(frame.index)
        first = int(np.searchsorted(index, shown)) if shown is not None else 0
        ind = indicators(frame)
        rows = signal_stats(ind, first)
        rows_by_symbol[code] = rows
        bench = _benchmark_returns(benchmark, index[first])
        close = frame["Close"].iloc[first:]
        returns[code] = (close / close.shift() - 1).dropna()
        symbols.append(
            {
                "symbol": code,
                "name": BIST_INDICES.get(code),
                "start": stamp(index[first]),
                "end": stamp(index[-1]),
                "behavior": behavior(ind, index, first, bench, interval),
                "signals": rows,
                "active_now": [r["key"] for r in rows if r["active_now"]],
            }
        )
    return {
        "interval": interval,
        "horizons": list(HORIZONS),
        "symbols": symbols,
        "ranking": rank_signals(rows_by_symbol),
        "correlation": correlation(returns),
        "catalog": [signal.describe() for signal in SIGNALS],
        "notes": [
            DEPTH_NOTE,
            "Getiriler sinyal barından sonraki açılıştan ölçüldü; 'fazla getiri'"
            " aynı dönemdeki tüm barların ortalamasına göre farktır.",
            f"{MIN_EVENTS}'den az olaylı sinyaller 'az örnek' sayıldı; t değeri"
            " çakışan dönemler yüzünden iyimserdir.",
        ],
    }


def load_study_frames(
    codes: list[str], start: date, end: date, interval: str = "1d"
) -> tuple[dict[str, tuple[pd.DataFrame, pd.Timestamp | None]], pd.DataFrame | None]:
    """Load each symbol (with warm-up bars) and the benchmark, in parallel."""

    def one(code: str):
        return code, load_bars(code, start, end, interval, warmup=True)

    def bench():
        try:
            return load_bars(BENCHMARK, start, end, interval, warmup=True)[0]
        except Exception:  # The study works without a benchmark.
            return None

    with ThreadPoolExecutor(max_workers=min(6, len(codes) + 1)) as pool:
        benchmark = pool.submit(bench)
        frames = dict(pool.map(one, codes))
        return frames, benchmark.result()


def prepare(
    symbols: list[str],
    cutoff: date | str | None = None,
    years: float = 2,
    interval: str = "1d",
) -> tuple[dict[str, Any], dict[str, tuple[pd.DataFrame, Any]], pd.DataFrame | None]:
    """Load and study the symbols; also return the frames and the benchmark."""
    if interval not in STUDY_INTERVALS:
        raise ValueError(f"Araştırma için zaman dilimi: {', '.join(STUDY_INTERVALS)}.")
    codes = _codes(symbols)
    start, end = study_window(cutoff, years)
    frames, benchmark = load_study_frames(codes, start, end, interval)
    result = study_frames(frames, benchmark, interval)
    result.update(cutoff=end.isoformat(), years=years, start=start.isoformat())
    return result, frames, benchmark


def study(
    symbols: list[str],
    cutoff: date | str | None = None,
    years: float = 2,
    interval: str = "1d",
) -> dict[str, Any]:
    """Study 1-8 symbols over ``years`` up to ``cutoff`` (inclusive).

    Nothing after ``cutoff`` is loaded, so the findings can drive a blind test
    that starts the next day.
    """
    return prepare(symbols, cutoff, years, interval)[0]


# --- Anonymous view ---------------------------------------------------------------


def letter(position: int) -> str:
    """Name the n-th hidden symbol: Hisse A, Hisse B, ..."""
    return f"Hisse {chr(ord('A') + position)}"


def anonymize(result: dict[str, Any]) -> dict[str, Any]:
    """Hide names, dates and price levels, so a model cannot recall the future.

    A language model may remember how a known stock moved after a date; with
    letters instead of tickers and no dates it can only use the numbers given.
    """
    names = {item["symbol"]: letter(k) for k, item in enumerate(result["symbols"])}
    symbols = []
    for item in result["symbols"]:
        behavior_ = dict(item["behavior"])
        liquidity = dict(behavior_.pop("liquidity"))
        liquidity.pop("median_traded_tl", None)
        behavior_["liquidity"] = liquidity
        behavior_.pop("weekday_mean_pct", None)
        symbols.append(
            {
                "symbol": names[item["symbol"]],
                "behavior": behavior_,
                "signals": [
                    {
                        k: v
                        for k, v in row.items()
                        if k not in ("active_now", "bars_since")
                    }
                    for row in item["signals"]
                    if row["events"]
                ],
            }
        )
    ranking = [
        {k: v for k, v in row.items() if k != "active_now"} for row in result["ranking"]
    ]
    corr = result.get("correlation")
    if corr:
        corr = {**corr, "symbols": [names[s] for s in corr["symbols"]]}
    return {
        "interval": result["interval"],
        "horizons": result["horizons"],
        "symbols": symbols,
        "ranking": ranking,
        "correlation": corr,
    }


def _fmt(value: Any, suffix: str = "") -> str:
    return "?" if value is None else f"{value}{suffix}"


def summary_text(anon: dict[str, Any], per_symbol: int = 12, top: int = 15) -> str:
    """Write an anonymous study as compact text for a language model."""
    lines = [
        f"Zaman dilimi: {anon['interval']}. Ufuklar: {anon['horizons']} bar."
        " Getiriler sinyalden sonraki açılıştan ölçüldü; 'fazla' = aynı dönemin"
        " ortalamasına göre fark (yüzde puan).",
    ]
    for item in anon["symbols"]:
        b = item["behavior"]
        liquidity = b.get("liquidity") or {}
        lines.append(
            f"\n{item['symbol']}: {b['bars']} bar, getiri %{_fmt(b['return_pct'])},"
            f" yıllık oynaklık %{_fmt(b['volatility_ann_pct'])},"
            f" maks. düşüş %{_fmt(b['max_drawdown_pct'])},"
            f" SMA200 üstünde geçen süre %{_fmt(b['above_sma200_pct'])},"
            f" ADX medyan {_fmt(b['adx_median'])},"
            f" otokorelasyon {_fmt(b['autocorr_1'])} ({b['tendency']}),"
            f" beta {_fmt(b.get('beta'))}, endekse göre %{_fmt(b.get('vs_benchmark_pct'))},"
            f" ort. bar hareketi %{_fmt(b['avg_move_pct'])},"
            f" ort. boşluk %{_fmt(b['gap_avg_pct'])},"
            f" likidite {liquidity.get('bucket', '?')}."
        )
        rows = [r for r in item["signals"] if r["events"] >= 3]
        rows.sort(key=lambda r: (r["strength"] == "az örnek", -abs(r["t"] or 0)))
        if rows:
            lines.append("  sinyal | olay | fazla getiri 5/10/20 | isabet 10 | t | güç")
        for row in rows[:per_symbol]:
            excess = "/".join(
                _fmt(row[f"h{h}"]["excess_pct"]) for h in anon["horizons"]
            )
            lines.append(
                f"  {row['key']} | {row['events']} | {excess} |"
                f" %{_fmt(row[f'h{MAIN_HORIZON}']['hit_pct'])} | {_fmt(row['t'])} |"
                f" {row['strength']}"
            )
    lines.append(
        f"\nTüm hisseler birlikte (olay ağırlıklı {MAIN_HORIZON} bar fazla getiri;"
        " skor = beklenen yönde fazla getiri):"
    )
    for row in [r for r in anon["ranking"] if r["events"]][:top]:
        lines.append(
            f"  {row['key']} ({row['label']}, {row['group']}, beklenen"
            f" {row['bias']}): olay {row['events']}, fazla %{_fmt(row['excess_pct'])},"
            f" skor {_fmt(row['score'])}, destekleyen hisse {row['agreeing_symbols']}"
        )
    corr = anon.get("correlation")
    if corr:
        lines.append(f"\nGetiri korelasyonu ortalaması: {corr['average']}.")
    return "\n".join(lines)
