"""Paper trading records."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Europe/Istanbul")

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop"]
TimeInForce = Literal["day", "gtc"]
OrderStatus = Literal["open", "filled", "cancelled", "expired", "rejected"]


def as_istanbul(moment: datetime) -> datetime:
    """Treat a naive time as Istanbul time; convert an aware one to Istanbul."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=IST)
    return moment.astimezone(IST)


def _plain(record: Any) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in asdict(record).items()
    }


@dataclass(frozen=True)
class Bar:
    """One completed price bar. ``start`` and ``end`` are Istanbul times."""

    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Order:
    """An order and, once it is done, how it ended."""

    id: int
    symbol: str
    side: Side
    qty: int
    type: OrderType
    tif: TimeInForce
    status: OrderStatus
    submitted_at: datetime
    limit_price: float | None = None
    stop_price: float | None = None
    session_date: date | None = None
    filled_at: datetime | None = None
    fill_price: float | None = None
    commission: float | None = None
    reason: str | None = None
    tag: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict."""
        return _plain(self)


@dataclass
class Fill:
    """An executed trade."""

    id: int
    order_id: int
    symbol: str
    side: Side
    qty: int
    price: float
    commission: float
    time: datetime

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict."""
        return _plain(self)


@dataclass
class Position:
    """Shares held in one symbol.

    ``avg_cost`` is the cost per share including buy commissions, so
    ``unrealized_pnl`` and ``realized_pnl`` are both net of costs.
    """

    symbol: str
    qty: int
    avg_cost: float
    realized_pnl: float
    last_price: float | None = None
    last_time: datetime | None = None

    @property
    def market_value(self) -> float | None:
        """Shares times the last price."""
        return None if self.last_price is None else self.qty * self.last_price

    @property
    def unrealized_pnl(self) -> float | None:
        """Gain or loss on the shares still held."""
        value = self.market_value
        return None if value is None else value - self.qty * self.avg_cost

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict."""
        return {
            **_plain(self),
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
        }
