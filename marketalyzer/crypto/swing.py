"""A leveraged trend account on 4-hour bars, long-only or short-only (paper only).

No martingale: every trade risks the same share of equity. A coin is
bought (sold short) when its 4-hour close breaks the highest (lowest) close
of the previous ``entry_bars`` bars and, with ``daily_filter``, its last
daily close is above (below) its ``trend_days``-day average. The stop starts
``stop_atr`` 4-hour ATRs away and trails the lowest low (highest high) of the
last ``exit_bars`` bars, only ever tightening. The position is sized so that
the stop costs ``risk`` of equity, at most ``max_position`` times equity per
coin and ``max_gross`` times equity in all, held on isolated margin at
``leverage``. Stops, targets of none and liquidation are checked inside each
bar (a bar that opens past the stop fills at its open). Fees and slippage are
paid on the position's value and funding every bar (longs pay a positive
rate, shorts receive it).

The functions here are shared by the backtest and the live runner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer.scripting import ta

BARS_PER_DAY = 6


@dataclass(frozen=True)
class Rules:
    """The account's rules; the defaults were fixed before any test."""

    side: str = "long"
    entry_bars: int = 30  # five days of 4-hour bars
    exit_bars: int = 15
    stop_atr: float = 2.0
    atr_bars: int = 14
    trend_days: int = 50
    daily_filter: bool = True
    risk: float = 0.01
    max_position: float = 1.0
    max_gross: float = 3.0
    max_positions: int = 5
    leverage: float = 3.0
    fee: float = 0.0005
    slippage: float = 0.0002
    maintenance: float = 0.005

    @property
    def sign(self) -> int:
        """Return +1 for longs and -1 for shorts."""
        return 1 if self.side == "long" else -1


@dataclass
class Position:
    """An open position."""

    symbol: str
    units: float
    entry: float
    stop: float
    margin: float
    liquidation: float
    risk_usd: float
    opened: str
    fees: float = 0.0
    funding: float = 0.0
    bars: int = 0


@dataclass
class Account:
    """Wallet (margins included), open positions and running totals."""

    wallet: float
    initial: float
    positions: dict[str, Position] = field(default_factory=dict)
    stats: dict[str, float] = field(default_factory=lambda: {
        "trades": 0, "wins": 0, "losses": 0, "liquidations": 0, "fees": 0.0,
        "funding": 0.0, "r_total": 0.0,
    })  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        """Return the account as JSON-friendly data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        """Rebuild an account stored with ``to_dict``."""
        data = dict(data)
        data["positions"] = {
            s: Position(**p) for s, p in data.get("positions", {}).items()
        }
        return cls(**data)


# --- Signals ------------------------------------------------------------------------


def signals(bars: pd.DataFrame, daily: pd.Series, rules: Rules) -> pd.DataFrame:
    """Return, per 4-hour bar (index = bar start), what is known at its close.

    Columns: ``enter`` (a breakout the filter allows), ``trail`` (the trailing
    stop level from the last ``exit_bars`` bars), ``atr`` and ``strength``
    (how far past the breakout level the close is, in ATRs).
    """
    sign = rules.sign
    close, high, low = (bars[k].to_numpy(dtype=float) for k in ("Close", "High", "Low"))
    atr = ta.atr(high, low, close, rules.atr_bars)
    roll = pd.Series(close).rolling(rules.entry_bars)
    level = (roll.max() if sign > 0 else roll.min()).shift(1).to_numpy()
    with np.errstate(invalid="ignore"):
        broke = sign * (close - level) > 0
        strength = sign * (close - level) / atr
    extreme = pd.Series(low if sign > 0 else high).rolling(rules.exit_bars)
    trail = (extreme.min() if sign > 0 else extreme.max()).to_numpy()
    allowed = np.ones(len(bars), dtype=bool)
    if rules.daily_filter:
        values = daily.to_numpy(dtype=float)
        average = ta.sma(values, rules.trend_days)
        with np.errstate(invalid="ignore"):
            trend = pd.Series(
                sign * (values - average) > 0, index=daily.index + pd.Timedelta(days=1)
            )
        ends = bars.index + pd.Timedelta(hours=4)
        trend = trend[~np.isnan(average)]
        known = trend.reindex(trend.index.union(ends)).ffill().reindex(ends)
        allowed = known.fillna(False).to_numpy(dtype=bool)
    return pd.DataFrame({"enter": broke & allowed & ~np.isnan(atr), "trail": trail,
                         "atr": atr, "strength": strength}, index=bars.index)  # fmt: skip


# --- Trading ------------------------------------------------------------------------


def value(account: Account, prices: dict[str, float], rules: Rules) -> float:
    """Return the wallet plus the open positions' profit (losses capped at margin)."""
    total = account.wallet
    for symbol, p in account.positions.items():
        price = prices.get(symbol, p.entry)
        total += max(rules.sign * p.units * (price - p.entry), -p.margin)
    return total


def gross(account: Account, prices: dict[str, float]) -> float:
    """Return the open positions' value."""
    return sum(p.units * prices.get(s, p.entry) for s, p in account.positions.items())


def check(account: Account, rules: Rules, symbol: str, start: float, high: float,
          low: float, time: str) -> dict | None:  # fmt: skip
    """Close ``symbol`` on its stop or liquidation touched inside a bar."""
    p = account.positions.get(symbol)
    if p is None:
        return None
    sign = rules.sign
    worst = low if sign > 0 else high
    if sign * (start - p.liquidation) <= 0:
        return close(
            account, rules, symbol, p.liquidation, "tasfiye", time, liquidated=True
        )
    levels = sorted([(p.stop, "stop"), (p.liquidation, "liquidation")],
                    key=lambda level: -sign * level[0])  # fmt: skip
    for level, kind in levels:
        if sign * (worst - level) <= 0:
            if kind == "liquidation":
                return close(
                    account, rules, symbol, level, "tasfiye", time, liquidated=True
                )
            fill = start if sign * (start - level) <= 0 else level
            return close(account, rules, symbol, fill, "iz süren stop", time)
    return None


def close(account: Account, rules: Rules, symbol: str, price: float, reason: str,
          time: str, liquidated: bool = False) -> dict:  # fmt: skip
    """Close a position and settle it."""
    p = account.positions.pop(symbol)
    sign = rules.sign
    if liquidated:
        exit_price, fee, gross_pnl = price, 0.0, -p.margin
    else:
        exit_price = price * (1 - sign * rules.slippage)
        fee = p.units * exit_price * rules.fee
        gross_pnl = max(sign * p.units * (exit_price - p.entry), -p.margin)
    account.wallet += gross_pnl - fee
    pnl = gross_pnl - fee - p.fees - p.funding
    stats = account.stats
    stats["fees"] += fee
    stats["trades"] += 1
    stats["wins" if pnl > 0 else "losses"] += 1
    stats["liquidations"] += liquidated
    r = pnl / p.risk_usd if p.risk_usd else 0.0
    stats["r_total"] += r
    return {
        "symbol": symbol, "side": rules.side, "entry": p.entry, "exit": exit_price,
        "opened": p.opened, "closed": time, "reason": reason, "bars": p.bars,
        "notional": round(p.units * p.entry, 2), "pnl": round(pnl, 2), "r": round(r, 3),
        "fees": round(p.fees + fee, 2), "funding": round(p.funding, 2),
    }  # fmt: skip


def fund(
    account: Account, rules: Rules, symbol: str, rate: float, price: float
) -> None:
    """Pay (or receive) funding at ``rate`` on a position's value."""
    p = account.positions.get(symbol)
    if p is None or not np.isfinite(rate):
        return
    cost = rules.sign * p.units * price * rate
    account.wallet -= cost
    p.funding += cost
    account.stats["funding"] += cost


def trail(account: Account, rules: Rules, symbol: str, level: float) -> None:
    """Tighten a position's stop to ``level`` when that is closer to the price."""
    p = account.positions.get(symbol)
    if p is not None and np.isfinite(level) and rules.sign * (level - p.stop) > 0:
        p.stop = level


def open_positions(account: Account, rules: Rules, candidates: list[dict],
                   prices: dict[str, float], time: str) -> list[str]:  # fmt: skip
    """Open the strongest candidates the limits allow; return what was opened.

    Each candidate is ``{"symbol", "close", "atr", "strength"}``.
    """
    opened = []
    sign = rules.sign
    equity = value(account, prices, rules)
    for c in sorted(candidates, key=lambda c: -c["strength"]):
        if len(account.positions) >= rules.max_positions or equity <= 0:
            break
        if c["symbol"] in account.positions or not c["atr"] > 0:
            continue
        entry = c["close"] * (1 + sign * rules.slippage)
        stop = entry - sign * rules.stop_atr * c["atr"]
        distance = abs(entry - stop)
        room = rules.max_gross * equity - gross(account, prices)
        size = min(
            rules.risk * equity / distance * entry, rules.max_position * equity, room
        )
        margin = size / rules.leverage
        free = account.wallet - sum(p.margin for p in account.positions.values())
        if size <= 0 or margin > free:
            continue
        fee = size * rules.fee
        account.wallet -= fee
        account.stats["fees"] += fee
        units = size / entry
        account.positions[c["symbol"]] = Position(
            symbol=c["symbol"], units=units, entry=entry, stop=stop, margin=margin,
            liquidation=entry * (1 - sign * (1 / rules.leverage - rules.maintenance)),
            risk_usd=units * distance, opened=time, fees=fee,
        )  # fmt: skip
        prices = {**prices, c["symbol"]: c["close"]}
        opened.append(c["symbol"])
    return opened
