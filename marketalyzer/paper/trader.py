"""Run a strategy against a paper account on live or replayed bars."""

import time as clock
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backtesting import Strategy

from marketalyzer.backtest.strategies import get_strategy
from marketalyzer.paper.account import OrderRejected, PaperAccount, normalize_symbol
from marketalyzer.paper.feed import Feed, FrameFeed
from marketalyzer.paper.models import IST, Fill, Order, as_istanbul
from marketalyzer.paper.signals import wants_long


@dataclass
class StepReport:
    """What happened in one trading step."""

    time: datetime
    bars: int = 0
    fills: list[Fill] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    expired: list[Order] = field(default_factory=list)
    signals: dict[str, bool] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict."""
        return {
            "time": self.time.isoformat(),
            "bars": self.bars,
            "fills": [fill.to_dict() for fill in self.fills],
            "orders": [order.to_dict() for order in self.orders],
            "expired": [order.to_dict() for order in self.expired],
            "signals": self.signals,
            "errors": self.errors,
        }


class PaperTrader:
    """Feed bars to a paper account and, optionally, trade a strategy's signals.

    Each step processes the bars that completed since the last step, then asks
    the strategy whether it wants to be long each symbol on daily bars. It buys
    with the symbol's share of equity when the strategy turns long and sells the
    whole position when it turns flat. Signals use completed daily bars only, so
    orders follow the backtest's timing: decided at a close, filled at the next
    open.
    """

    def __init__(
        self,
        account: PaperAccount,
        symbols: list[str],
        feed: Feed,
        strategy: str | type[Strategy] | None = None,
        params: dict[str, Any] | None = None,
        weights: dict[str, float] | None = None,
        cash_buffer: float = 0.02,
    ):
        self.account = account
        self.symbols = list(dict.fromkeys(normalize_symbol(s) for s in symbols))
        if not self.symbols:
            raise ValueError("Give at least one symbol.")
        self.feed = feed
        self.strategy = get_strategy(strategy) if strategy else None
        self.params = params or {}
        self.tag = f"signal:{self.strategy.__name__}" if self.strategy else None
        if weights:
            self.weights = {normalize_symbol(s): w for s, w in weights.items()}
        else:
            self.weights = {s: 1 / len(self.symbols) for s in self.symbols}
        if sum(self.weights.values()) > 1 + 1e-9:
            raise ValueError("Weights must not add up to more than 1.")
        if not 0 <= cash_buffer < 1:
            raise ValueError("cash_buffer must be between 0 and 1.")
        self.cash_buffer = cash_buffer
        self._signals: dict[tuple[str, Any], bool] = {}

    def step(self, now: datetime | None = None) -> StepReport:
        """Process new bars, then act on the strategy's signals."""
        now = as_istanbul(now or datetime.now(IST))
        report = StepReport(time=now)
        for symbol in self.symbols:
            try:
                since = self.account.processed_until(symbol)
                bars = self.feed.bars(symbol, since, now)
            except Exception as error:
                report.errors[symbol] = f"{type(error).__name__}: {error}"
                continue
            for bar in bars:
                report.fills.extend(self.account.process_bar(symbol, bar))
            report.bars += len(bars)
        report.expired = self.account.expire_day_orders(now)
        if report.bars or report.fills:
            self.account.record_equity(now)

        if self.strategy:
            for symbol in self.symbols:
                if symbol in report.errors:
                    continue
                try:
                    report.signals[symbol] = want = self._signal(symbol, now)
                    order = self._rebalance(symbol, want, now)
                except OrderRejected as error:
                    report.errors[symbol] = str(error)
                    continue
                except Exception as error:
                    report.errors[symbol] = f"{type(error).__name__}: {error}"
                    continue
                if order:
                    report.orders.append(order)
        return report

    def run(
        self,
        poll: float = 60,
        steps: int | None = None,
        on_step: Callable[[StepReport], None] | None = None,
    ) -> None:
        """Step every ``poll`` seconds, forever or for ``steps`` steps."""
        count = 0
        while True:
            report = self.step()
            if on_step:
                on_step(report)
            count += 1
            if steps is not None and count >= steps:
                return
            clock.sleep(poll)

    def _signal(self, symbol: str, now: datetime) -> bool:
        """Return the strategy's wish for a symbol, computed once per daily bar."""
        daily = self.feed.daily(symbol, now)
        key = (symbol, daily.index[-1] if len(daily) else None)
        if key not in self._signals:
            self._signals[key] = wants_long(self.strategy, daily, self.params)
        return self._signals[key]

    def _rebalance(self, symbol: str, want: bool, now: datetime) -> Order | None:
        """Place the order that moves a symbol toward what the strategy wants."""
        open_orders = self.account.orders(status="open", symbol=symbol)
        if any(order.tag == self.tag for order in open_orders):
            return None
        held = self.account.held(symbol)
        if not want and held:
            pending = sum(o.qty for o in open_orders if o.side == "sell")
            if held - pending > 0:
                return self.account.submit(
                    symbol, "sell", held - pending, tag=self.tag, now=now
                )
        if want and not held:
            price = self.account.mark(symbol)
            if price is None:
                return None
            costs, slippage = self.account.costs, self.account.slippage
            unit = price * (1 + slippage / 2) * (1 + costs.effective_rate)
            budget = min(
                self.account.equity() * self.weights.get(symbol, 0.0),
                self.account.buying_power(),
            ) * (1 - self.cash_buffer)
            qty = int(budget // unit)
            if qty >= 1:
                return self.account.submit(symbol, "buy", qty, tag=self.tag, now=now)
        return None


def replay(
    account: PaperAccount,
    feed: FrameFeed,
    symbols: list[str],
    strategy: str | type[Strategy] | None = None,
    params: dict[str, Any] | None = None,
    weights: dict[str, float] | None = None,
    on_step: Callable[[StepReport], None] | None = None,
) -> list[StepReport]:
    """Run the paper trader over historical bars as if they arrived live.

    The clock steps through every bar end and every session close, so signals
    decided at a close are filled at the next session's first bar, as in live
    trading.
    """
    trader = PaperTrader(account, symbols, feed, strategy, params, weights)
    reports = []
    for moment in feed.times(include_closes=True):
        report = trader.step(moment)
        reports.append(report)
        if on_step:
            on_step(report)
    return reports
