"""Borsa Istanbul trading costs and price ticks."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal

# BIST equity market price ticks as (lower bound of the price band, tick).
# Check against Borsa Istanbul's current announcement before relying on them.
TICK_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("2500"), Decimal("2.50")),
    (Decimal("1000"), Decimal("1.00")),
    (Decimal("500"), Decimal("0.50")),
    (Decimal("250"), Decimal("0.25")),
    (Decimal("100"), Decimal("0.10")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("20"), Decimal("0.02")),
    (Decimal("0"), Decimal("0.01")),
)

_ROUNDING = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}


def tick_size(price: float) -> float:
    """Return the BIST price tick for a price."""
    value = Decimal(str(price))
    for lower, tick in TICK_BANDS:
        if value >= lower:
            return float(tick)
    raise ValueError(f"Price must not be negative: {price}")


def round_to_tick(
    price: float, direction: Literal["nearest", "down", "up"] = "nearest"
) -> float:
    """Round a price onto the BIST tick grid, e.g. for a limit order.

    Use "down" for buy limits and "up" for sell limits to stay on the safe side.
    """
    tick = Decimal(str(tick_size(price)))
    steps = (Decimal(str(price)) / tick).quantize(
        Decimal(1), rounding=_ROUNDING[direction]
    )
    return float(steps * tick)


@dataclass(frozen=True)
class BistCosts:
    """BIST transaction costs, passed to backtesting.py as ``commission``.

    backtesting.py charges it on both the entry and the exit of every trade.

    Parameters
    ----------
    commission_rate
        Broker commission on the order value. 0.002 is "binde 2".
    bsmv_rate
        Banking and insurance transactions tax (BSMV), charged on the commission.
    min_commission
        Minimum commission per order in TRY, before BSMV.
    exchange_fee_rate
        Exchange and clearing fees on the order value, if the broker does not
        already include them in the commission.
    """

    commission_rate: float = 0.002
    bsmv_rate: float = 0.05
    min_commission: float = 0.0
    exchange_fee_rate: float = 0.0

    @property
    def effective_rate(self) -> float:
        """Cost per unit of order value, ignoring the minimum commission."""
        return self.commission_rate * (1 + self.bsmv_rate) + self.exchange_fee_rate

    def __call__(self, order_size: float, price: float) -> float:
        """Return the cost in TRY of an order of ``order_size`` shares."""
        value = abs(order_size) * price
        commission = value * self.commission_rate
        # backtesting.py also calls this with a fractional size (the share of
        # equity to invest) to estimate the per-share cost while sizing an order.
        # A per-order minimum would be spread over a fraction of a share there and
        # shrink the order, so it only applies to real orders of whole shares.
        if abs(order_size) >= 1:
            commission = max(commission, self.min_commission)
        return commission * (1 + self.bsmv_rate) + value * self.exchange_fee_rate
