"""A leveraged one-position account with a martingale leverage ladder (paper only).

Each account trades one direction (long or short) and holds at most one
isolated-margin perpetual position at a time. A cycle starts with ``margin``
of the wallet as margin at the ladder's first leverage. A losing trade moves
the next one a step up the ladder (2x → 4x → 8x → 10x) with the same margin;
once the cycle's trades add up to a profit (losses won back plus a gain) the
cycle closes and the ladder starts again. A loss on the last step ends the
cycle with that loss.

A position closes on its stop (``stop_atr`` 15-minute ATRs away), its target
(``take_atr`` ATRs), liquidation (the margin is lost), a fading score or
after ``max_bars`` bars. Fees and slippage are paid on the position's value
and funding on its value every eight hours (longs pay a positive rate, shorts
receive it).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Config:
    """How an account trades; the defaults are the experiment's settings."""

    side: str = "long"  # "long" or "short"
    ladder: tuple[float, ...] = (2.0, 4.0, 8.0, 10.0)
    margin: float = 0.20  # share of the wallet put up as margin at a cycle's start
    enter: float = 70.0  # minimum score to open
    leave: float = 45.0  # close at a bar's end when the score falls below this
    stop_atr: float = 1.0
    take_atr: float = 1.5
    max_bars: int = 16  # 15-minute bars (four hours)
    fee: float = 0.0005  # per side, taker
    slippage: float = 0.0002  # per side
    maintenance: float = 0.005
    floor: float = 0.10  # stop opening below this share of the starting wallet

    @property
    def sign(self) -> int:
        """Return +1 for the long account and -1 for the short one."""
        return 1 if self.side == "long" else -1


@dataclass
class Position:
    """The open position."""

    symbol: str
    leverage: float
    margin: float
    entry: float
    units: float
    stop: float
    take: float
    liquidation: float
    opened: str
    score: float
    step: int
    bars: int = 0
    fees: float = 0.0
    funding: float = 0.0


@dataclass
class Book:
    """The account: wallet (margin included), ladder state and open position."""

    wallet: float
    initial: float
    step: int = 0
    cycle_margin: float | None = None
    cycle_pnl: float = 0.0
    position: Position | None = None
    stats: dict[str, float] = field(default_factory=lambda: {
        "trades": 0, "wins": 0, "losses": 0, "liquidations": 0, "cycles_won": 0,
        "cycles_lost": 0, "fees": 0.0, "funding": 0.0, "worst_cycle": 0.0,
    })  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        """Return the book as JSON-friendly data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Book:
        """Rebuild a book stored with ``to_dict``."""
        data = dict(data)
        if data.get("position"):
            data["position"] = Position(**data["position"])
        return cls(**data)


def unrealized(position: Position, price: float, sign: int) -> float:
    """Return the open profit at ``price``; a loss is capped at the margin."""
    return max(sign * position.units * (price - position.entry), -position.margin)


def equity(book: Book, price: float | None, sign: int) -> float:
    """Return the wallet plus the open profit."""
    if book.position is None or price is None:
        return book.wallet
    return book.wallet + unrealized(book.position, price, sign)


def can_open(book: Book, config: Config) -> bool:
    """Return whether the account may open a position."""
    return book.position is None and book.wallet > config.floor * book.initial


def open_position(book: Book, config: Config, symbol: str, price: float, atr: float,
                  score: float, time: str) -> Position:  # fmt: skip
    """Open at ``price`` with the ladder's current leverage."""
    sign = config.sign
    if book.cycle_margin is None:
        book.cycle_margin = config.margin * book.wallet
    margin = min(book.cycle_margin, book.wallet)
    leverage = config.ladder[book.step]
    entry = price * (1 + sign * config.slippage)
    size = margin * leverage
    fee = size * config.fee
    book.wallet -= fee
    book.stats["fees"] += fee
    position = Position(
        symbol=symbol, leverage=leverage, margin=margin - fee, entry=entry,
        units=size / entry, stop=entry - sign * config.stop_atr * atr,
        take=entry + sign * config.take_atr * atr,
        liquidation=entry * (1 - sign * (1 / leverage - config.maintenance)),
        opened=time, score=round(score, 1), step=book.step, fees=fee,
    )  # fmt: skip
    book.position = position
    return position


def check(book: Book, config: Config, start: float, high: float, low: float,
          time: str) -> dict | None:  # fmt: skip
    """Close on the stop, liquidation or target touched inside a bar.

    ``start`` is the bar's open. Against the position, the price meets
    whichever of the stop and the liquidation price is nearer first; a bar
    that opens past a level fills there at the open (past the liquidation
    price, the margin is lost). When both a loss level and the target are in
    the bar's range, the loss is assumed to come first.
    """
    p = book.position
    if p is None:
        return None
    sign = config.sign
    worst, best = (low, high) if sign > 0 else (high, low)
    if sign * (start - p.liquidation) <= 0:
        return close(book, config, p.liquidation, "tasfiye", time, liquidated=True)
    levels = sorted(
        [(p.stop, "stop"), (p.liquidation, "liquidation")],
        key=lambda level: -sign * level[0],
    )  # nearest to the price first
    for level, kind in levels:
        if sign * (worst - level) <= 0:
            if kind == "liquidation":
                return close(book, config, level, "tasfiye", time, liquidated=True)
            fill = start if sign * (start - level) <= 0 else level
            return close(book, config, fill, "zarar durdur", time)
    if sign * (best - p.take) >= 0:
        fill = start if sign * (start - p.take) >= 0 else p.take
        return close(book, config, fill, "kâr al", time)
    return None


def fund(book: Book, config: Config, rate: float, price: float) -> float:
    """Pay (or receive) one funding interval on the open position."""
    p = book.position
    if p is None:
        return 0.0
    cost = config.sign * p.units * price * rate
    book.wallet -= cost
    p.funding += cost
    book.stats["funding"] += cost
    return cost


def close(book: Book, config: Config, price: float, reason: str, time: str,
          liquidated: bool = False) -> dict:  # fmt: skip
    """Close the position, settle it and move the martingale ladder."""
    p = book.position
    sign = config.sign
    if liquidated:
        exit_price, fee, gross = price, 0.0, -p.margin
    else:
        exit_price = price * (1 - sign * config.slippage)
        fee = p.units * exit_price * config.fee
        gross = max(sign * p.units * (exit_price - p.entry), -p.margin)
    book.wallet += gross - fee
    book.stats["fees"] += fee
    pnl = gross - fee - p.fees - p.funding  # the whole trade, entry fee and funding too
    book.position = None
    book.cycle_pnl += pnl
    stats = book.stats
    stats["trades"] += 1
    stats["wins" if pnl > 0 else "losses"] += 1
    stats["liquidations"] += liquidated
    trade = {
        "symbol": p.symbol, "side": config.side, "leverage": p.leverage, "step": p.step,
        "margin": round(p.margin + p.fees, 2), "entry": p.entry, "exit": exit_price,
        "opened": p.opened, "closed": time, "reason": reason, "score": p.score,
        "pnl": round(pnl, 2), "roe_pct": round(pnl / (p.margin + p.fees) * 100, 2),
        "fees": round(p.fees + fee, 2), "funding": round(p.funding, 2),
    }  # fmt: skip
    if book.cycle_pnl >= 0:
        stats["cycles_won"] += 1
        _new_cycle(book)
        trade["cycle"] = "kazanıldı"
    elif book.step == len(config.ladder) - 1:
        stats["cycles_lost"] += 1
        stats["worst_cycle"] = min(stats["worst_cycle"], round(book.cycle_pnl, 2))
        trade["cycle"] = "kaybedildi"
        _new_cycle(book)
    elif pnl < 0:
        book.step += 1
        trade["cycle"] = f"merdiven {config.ladder[book.step]:g}x"
    else:
        trade["cycle"] = "sürüyor"
    return trade


def _new_cycle(book: Book) -> None:
    book.step, book.cycle_margin, book.cycle_pnl = 0, None, 0.0
