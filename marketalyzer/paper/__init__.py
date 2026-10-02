"""Paper trading on Borsa Istanbul: a virtual account, no real money."""

from marketalyzer.paper.account import OrderRejected, PaperAccount, normalize_symbol
from marketalyzer.paper.feed import FrameFeed, ProviderFeed
from marketalyzer.paper.models import Bar, Fill, Order, Position
from marketalyzer.paper.signals import wants_long
from marketalyzer.paper.trader import PaperTrader, StepReport, replay

__all__ = [
    "Bar",
    "Fill",
    "FrameFeed",
    "Order",
    "OrderRejected",
    "PaperAccount",
    "PaperTrader",
    "Position",
    "ProviderFeed",
    "StepReport",
    "normalize_symbol",
    "replay",
    "wants_long",
]
