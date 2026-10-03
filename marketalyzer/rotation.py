"""Momentum rotation: each month, hold the stocks that rose the most.

At the last close of every month the stocks are ranked by their return over
the past ``lookback_months`` (leaving out the last ``skip_months``, which tend
to reverse); the top ``top`` are held in equal slots from the next day's open.
Only bars up to the decision close are used, so the ranking never sees the
future. Stocks that leave the top are sold and new ones bought at the open,
in whole lots and with BIST costs; stocks that stay are left alone, which
keeps the turnover low.

``absolute`` keeps a slot in cash instead of buying a stock below its 200-day
average; ``market`` holds only cash while XU100 is below its 200-day average.

Ranking stocks by past return (cross-sectional momentum) is one of the
best-documented effects in equities, and on Borsa Istanbul it has been a
significant factor since 2005. Picking from today's large caps carries
survivorship bias: stocks that fell out of the index are not in the list.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer import research
from marketalyzer.backtest.costs import BistCosts
from marketalyzer.paper.account import normalize_symbol
from marketalyzer.services import number, stamp, thin, today

MONTH = 21  # trading days
MAX_SYMBOLS = 30
LOOKBACKS = range(3, 13)
REGIME = 200  # bars in the trend filters' average
END = "dönem sonu (açık)"
# Turkey's large caps; a starting universe, with survivorship bias.
LARGE_CAPS = [
    "AKBNK", "GARAN", "ISCTR", "YKBNK", "KCHOL", "SAHOL", "SISE", "EREGL", "FROTO", "TOASO",
    "TCELL", "TUPRS", "THYAO", "BIMAS", "ASELS", "ENKAI", "PGSUS", "ARCLK", "PETKM", "KRDMD",
]  # fmt: skip


@dataclass
class RotationConfig:
    """What to rotate through, over which period and how."""

    symbols: list[str]
    start: date
    end: date
    lookback_months: int = 6
    skip_months: int = 1
    top: int = 5
    absolute: bool = False
    market: bool = False
    cash: float = 100_000.0
    costs: BistCosts = field(default_factory=BistCosts)
    slippage: float = 0.001

    def codes(self) -> list[str]:
        """Return the symbols as BIST codes, without repeats."""
        return list(
            dict.fromkeys(normalize_symbol(s) for s in self.symbols if str(s).strip())
        )

    def check(self) -> None:
        """Raise ValueError (in Turkish) for settings that cannot run."""
        count = len(self.codes())
        if not 2 <= count <= MAX_SYMBOLS:
            raise ValueError(
                f"Rotasyon için en az 2, en fazla {MAX_SYMBOLS} hisse seçin."
            )
        if not 1 <= self.top <= count:
            raise ValueError("Tutulacak hisse sayısı 1 ile hisse sayısından az olmalı.")
        if self.lookback_months not in LOOKBACKS:
            raise ValueError("Momentum ufku 3 ile 12 ay arasında olmalı.")
        if not 0 <= self.skip_months <= 2 or self.skip_months >= self.lookback_months:
            raise ValueError(
                "Atlanan son aylar 0, 1 ya da 2 olmalı ve ufuktan kısa olmalı."
            )
        if self.start >= self.end:
            raise ValueError("Başlangıç bitişten önce olmalı.")
        if self.end > today():
            raise ValueError("Bitiş bugünden sonra olamaz.")


@dataclass
class Prices:
    """Daily closes and opens of every symbol on one calendar, and XU100's closes."""

    closes: pd.DataFrame  # forward-filled over days a symbol did not trade
    opens: pd.DataFrame  # NaN where a symbol did not trade
    index_closes: pd.Series | None


def load(config: RotationConfig) -> Prices:
    """Load enough bars before ``start`` for the ranking and the 200-day average."""
    codes = config.codes()
    days = round((config.lookback_months + 1) * 31 + REGIME * 1.5) + 30
    frames, benchmark = research.load_study_frames(
        codes, config.start - timedelta(days=days), config.end, "1d"
    )
    closes = pd.concat({c: frames[c][0]["Close"] for c in codes}, axis=1, sort=True)
    opens = pd.concat({c: frames[c][0]["Open"] for c in codes}, axis=1, sort=True)
    closes = closes[closes.index <= pd.Timestamp(config.end)].ffill()
    opens = opens.reindex(closes.index)
    index_closes = None
    if benchmark is not None and not benchmark.empty:
        index_closes = benchmark["Close"].reindex(closes.index).ffill()
    return Prices(closes, opens, index_closes)


def momentum_scores(
    closes: pd.DataFrame, i: int, lookback: int, skip: int
) -> pd.Series:
    """Return each stock's return from ``lookback`` to ``skip`` bars before bar ``i``."""
    if i - lookback < 0:
        return pd.Series(dtype=float)
    return (closes.iloc[i - skip] / closes.iloc[i - lookback] - 1).dropna()


class _Book:
    """Cash, whole-lot positions and the trades closed so far."""

    def __init__(self, config: RotationConfig, prices: Prices):
        self.config = config
        self.prices = prices
        self.cash = config.cash
        self.qty: dict[str, int] = {}
        self.entry: dict[str, tuple[float, int, float]] = {}  # price, bar, fees
        self.trades: list[dict[str, Any]] = []

    def value(self, i: int, row: str = "closes") -> float:
        frame = getattr(self.prices, row)
        total = self.cash
        for code, qty in self.qty.items():
            price = frame[code].iloc[i]
            if not math.isfinite(price):
                price = self.prices.closes[code].iloc[i - 1]
            total += qty * float(price)
        return total

    def sell(self, i: int, code: str, price: float, reason: str) -> None:
        price *= 1 - self.config.slippage / 2
        qty = self.qty.pop(code)
        fee = self.config.costs(qty, price)
        self.cash += qty * price - fee
        entry_price, entry_bar, entry_fee = self.entry.pop(code)
        pnl = qty * (price - entry_price) - entry_fee - fee
        index = self.prices.closes.index
        self.trades.append(
            {
                "symbol": code,
                "entry_time": stamp(index[entry_bar]),
                "exit_time": stamp(index[i]),
                "qty": qty,
                "entry_price": number(entry_price),
                "exit_price": number(price),
                "pnl": number(pnl, 2),
                "return_pct": number(pnl / (qty * entry_price) * 100, 2),
                "bars": i - entry_bar,
                "exit_reason": reason,
            }
        )

    def buy(self, i: int, code: str, budget: float) -> bool:
        price = float(self.prices.opens[code].iloc[i]) * (1 + self.config.slippage / 2)
        per_share = price * (1 + self.config.costs.effective_rate)
        qty = int(min(budget, self.cash) // per_share)
        if qty <= 0:
            return False
        fee = self.config.costs(qty, price)
        self.cash -= qty * price + fee
        self.qty[code] = qty
        self.entry[code] = (price, i, fee)
        return True


def _choose(prices: Prices, i: int, config: RotationConfig) -> dict[str, Any]:
    """Rank on the close of bar ``i`` and pick the slots, with the filters."""
    closes = prices.closes
    scores = momentum_scores(
        closes, i, config.lookback_months * MONTH, config.skip_months * MONTH
    )
    ranked = scores.sort_values(ascending=False)
    average = closes.iloc[max(0, i - REGIME + 1) : i + 1].mean()
    above = closes.iloc[i] > average
    market_up = True
    if prices.index_closes is not None and i + 1 >= REGIME:
        window = prices.index_closes.iloc[i - REGIME + 1 : i + 1]
        market_up = bool(prices.index_closes.iloc[i] > window.mean())
    picks = []
    for code in ranked.index[: config.top]:
        if config.absolute and not above[code]:
            continue
        picks.append(
            {
                "symbol": code,
                "momentum_pct": number(ranked[code] * 100, 2),
                "above_200": bool(above[code]),
            }
        )
    if config.market and not market_up:
        picks = []
    ranking = [
        {
            "symbol": code,
            "momentum_pct": number(value * 100, 2),
            "above_200": bool(above[code]),
        }
        for code, value in ranked.items()
    ]
    return {"picks": picks, "ranking": ranking, "market_up": market_up}


def _rebalance(book: _Book, i: int, chosen: dict[str, Any], top: int) -> dict:
    """Sell the stocks that left the picks and buy the new ones at bar ``i``'s open."""
    opens = book.prices.opens
    wanted = [p["symbol"] for p in chosen["picks"]]
    sold, bought = [], []
    for code in list(book.qty):
        if code not in wanted and math.isfinite(opens[code].iloc[i]):
            book.sell(i, code, float(opens[code].iloc[i]), "rotasyon")
            sold.append(code)
    slot = book.value(i, "opens") / top
    for code in wanted:
        if code in book.qty or not math.isfinite(opens[code].iloc[i]):
            continue
        if book.buy(i, code, slot):
            bought.append(code)
    return {"sold": sold, "bought": bought}


def run_rotation(
    config: RotationConfig, prices: Prices | None = None
) -> dict[str, Any]:
    """Run the rotation from ``start`` to ``end`` and return the results."""
    config.check()
    prices = prices or load(config)
    index = prices.closes.index
    first = int(np.searchsorted(index, pd.Timestamp(config.start)))
    needed = config.lookback_months * MONTH
    if first < needed:
        raise ValueError("Başlangıçtan önce momentum için yeterli geçmiş yok.")
    if first >= len(index) - 1:
        raise ValueError("Test döneminde yeterli bar yok.")
    last = len(index) - 1
    decisions = {first - 1} | {
        i for i in range(first, last) if index[i].month != index[i + 1].month
    }
    book = _Book(config, prices)
    rebalances: list[dict[str, Any]] = []
    equity: list[tuple[Any, float]] = []
    invested = 0.0
    pending = _choose(prices, first - 1, config)
    pending_from = first - 1
    for i in range(first, last + 1):
        if pending is not None:
            done = _rebalance(book, i, pending, config.top)
            rebalances.append(
                {
                    "decided": stamp(index[pending_from]),
                    "executed": stamp(index[i]),
                    "picks": pending["picks"],
                    "cash_slots": config.top - len(pending["picks"]),
                    "market_up": pending["market_up"],
                    **done,
                }
            )
            pending = None
        value = book.value(i)
        equity.append((index[i], value))
        invested += (value - book.cash) / value if value else 0.0
        if i in decisions and i < last:
            pending, pending_from = _choose(prices, i, config), i
    for code in list(book.qty):
        book.sell(last, code, float(prices.closes[code].iloc[last]), END)
        book.trades[-1]["open"] = True
    curve = pd.Series(dict(equity))
    exposure = invested / len(equity) if equity else 0.0
    found = _metrics(curve, book.trades, exposure, config.cash)
    current = _choose(prices, last, config)
    return {
        "period": {
            "start": stamp(index[first]),
            "end": stamp(index[last]),
            "bars": last - first + 1,
        },
        "config": {
            "symbols": config.codes(),
            "lookback_months": config.lookback_months,
            "skip_months": config.skip_months,
            "top": config.top,
            "absolute": config.absolute,
            "market": config.market,
            "cash": config.cash,
        },
        "rotation": {**found, "equity": _points(curve)},
        "equal": _equal_weight(prices, first, config),
        "benchmark": _benchmark(prices, first, config.cash),
        "rebalances": rebalances,
        "trades": book.trades,
        "current": {"as_of": stamp(index[last]), **current},
        "notes": [
            "Her ay son kapanışta yalnızca o güne kadarki fiyatlarla sıralandı; işlemler"
            " ertesi günün açılışında, tam lot ve BIST maliyetleriyle yapıldı.",
            "Hisse listesi bugünün büyük şirketlerinden seçildiyse sonuç iyimserdir:"
            " dönem içinde endeksten düşen hisseler listede yok.",
        ],
    }


def _metrics(curve: pd.Series, trades, exposure: float, cash: float) -> dict[str, Any]:
    from marketalyzer.blind import metrics

    return metrics(curve, trades, 252, exposure, cash)


def _points(curve: pd.Series) -> list[dict[str, Any]]:
    return thin([{"t": stamp(t), "v": number(v, 2)} for t, v in curve.items()])


def _equal_weight(prices: Prices, first: int, config: RotationConfig) -> dict[str, Any]:
    """Buy every stock in equal parts at the first open and hold."""
    codes = list(prices.closes.columns)
    part = config.cash / len(codes)
    total = pd.Series(0.0, index=prices.closes.index[first:])
    for code in codes:
        price = prices.opens[code].iloc[first]
        if not math.isfinite(price):
            total += part
            continue
        price *= 1 + config.slippage / 2
        qty = int(part // (price * (1 + config.costs.effective_rate)))
        rest = part - qty * price - (config.costs(qty, price) if qty else 0.0)
        total += rest + qty * prices.closes[code].iloc[first:]
    return {
        "return_pct": number((total.iloc[-1] / config.cash - 1) * 100, 2),
        "equity": _points(total),
    }


def _benchmark(prices: Prices, first: int, cash: float) -> dict[str, Any] | None:
    if prices.index_closes is None:
        return None
    closes = prices.index_closes.iloc[first:].dropna()
    if len(closes) < 2:
        return None
    scaled = closes / closes.iloc[0] * cash
    return {
        "symbol": research.BENCHMARK,
        "return_pct": number((closes.iloc[-1] / closes.iloc[0] - 1) * 100, 2),
        "equity": _points(scaled),
    }
