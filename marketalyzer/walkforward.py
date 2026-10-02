"""Walk-forward testing: tune on the past, paper trade the period that follows."""

import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from backtesting import Strategy

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.data import load_ohlcv
from marketalyzer.backtest.engine import optimize_backtest
from marketalyzer.backtest.strategies import get_strategy
from marketalyzer.paper.account import (
    DIVIDEND_TAX,
    SESSION_CLOSE,
    PaperAccount,
    normalize_symbol,
)
from marketalyzer.paper.feed import FrameFeed
from marketalyzer.paper.models import IST
from marketalyzer.paper.trader import PaperTrader, replay_with


@dataclass
class Window:
    """One training period and the forward period traded with its parameters."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date
    params: dict[str, Any]
    in_sample_return_pct: float | None
    in_sample_sharpe: float | None
    forward_return_pct: float
    buy_hold_return_pct: float
    trades: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict."""
        return {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in asdict(self).items()
        }


@dataclass
class WalkForwardReport:
    """The forward (out-of-sample) result of a walk-forward test."""

    symbol: str
    strategy: str
    windows: list[Window]
    account: dict[str, Any]
    equity_curve: list[dict[str, Any]] = field(repr=False)
    buy_hold_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    trades: int = 0
    commissions: float = 0.0
    account_path: str | None = None

    def summary(self) -> dict[str, Any]:
        """Key results as a JSON-friendly dict; amounts are in TRY."""
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "start": self.windows[0].test_start.isoformat(),
            "end": self.windows[-1].test_end.isoformat(),
            "windows": [window.to_dict() for window in self.windows],
            "equity": self.account["equity"],
            "return_pct": self.account["return_pct"],
            "buy_hold_return_pct": round(self.buy_hold_return_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "trades": self.trades,
            "commissions": round(self.commissions, 2),
            "dividends": self.account["dividends"],
            "account": self.account_path,
        }


def split_windows(
    index: pd.DatetimeIndex, train_bars: int, test_bars: int
) -> list[tuple[pd.DatetimeIndex, pd.DatetimeIndex]]:
    """Return (train, test) date ranges that roll forward by ``test_bars``.

    The last test range may be shorter than ``test_bars``.
    """
    if train_bars < 2 or test_bars < 1:
        raise ValueError("train_bars must be at least 2 and test_bars at least 1.")
    if len(index) <= train_bars:
        raise ValueError(
            f"Need more than {train_bars} daily bars for one window, got {len(index)}."
        )
    windows = []
    for first in range(0, len(index) - train_bars, test_bars):
        train = index[first : first + train_bars]
        test = index[first + train_bars : first + train_bars + test_bars]
        windows.append((train, test))
    return windows


def _change(first: float, last: float) -> float:
    return (last / first - 1) * 100


def _max_drawdown(curve: list[dict[str, Any]]) -> float:
    if not curve:
        return 0.0
    equity = pd.Series([point["equity"] for point in curve])
    return float((equity / equity.cummax() - 1).min() * 100)


def walk_forward(
    symbol: str,
    strategy: str | type[Strategy] = "sma_cross",
    *,
    start: date | str,
    end: date | str | None = None,
    train_bars: int = 504,
    test_bars: int = 126,
    param_grid: dict[str, list] | None = None,
    maximize: str = "Sharpe Ratio",
    cash: float = 100_000,
    costs: BistCosts | None = None,
    slippage: float = 0.001,
    dividend_tax: float = DIVIDEND_TAX,
    account_path: Path | None = None,
    signals: pd.DataFrame | None = None,
    bars: pd.DataFrame | None = None,
    on_window: Callable[[Window], None] | None = None,
) -> WalkForwardReport:
    """Tune a strategy on a rolling past window and paper trade the next one.

    For each window the strategy's parameters are optimized by backtest on the
    previous ``train_bars`` daily bars. They are then used to trade the next
    ``test_bars`` days in one continuing paper account, with daily bars, BIST
    costs and dividends paid in cash. Only those forward days count in the
    result, so it measures how the tuning would have done on unseen data.

    ``signals`` (dividend-adjusted, what strategies see) and ``bars`` (split-
    adjusted with a ``Dividend`` column, what orders fill against) are loaded
    from the provider unless given.
    """
    symbol = normalize_symbol(symbol)
    strategy_cls = get_strategy(strategy)
    if signals is None:
        signals = load_ohlcv(symbol, start, end)
    if bars is None:
        bars = load_ohlcv(
            symbol, start, end, adjustment="splits_only", include_actions=True
        )
    windows = split_windows(signals.index, train_bars, test_bars)
    feed = FrameFeed({symbol: bars}, {symbol: signals}, interval="1d")

    with tempfile.TemporaryDirectory() as tmp:
        path = account_path or Path(tmp) / "walkforward.sqlite"
        first_day = windows[0][1][0].date()
        opened = datetime.combine(first_day - timedelta(days=1), time(9), IST)
        with PaperAccount.create(
            path, cash, costs, slippage, now=opened, dividend_tax=dividend_tax
        ) as account:
            trader = PaperTrader(account, [symbol], feed, strategy_cls)
            results = []
            for train, test in windows:
                tuned = optimize_backtest(
                    strategy=strategy_cls,
                    data=signals.loc[train],
                    param_grid=param_grid,
                    maximize=maximize,
                    holdout=None,
                    cash=cash,
                    costs=costs,
                    slippage=slippage,
                    benchmark=None,
                    usd=False,
                )
                trader.use_params(tuned.best_params)
                before = account.equity()
                fills_before = len(account.fills())
                # Start at the last training close: the new parameters decide
                # there and their first order fills at the first test open.
                replay_with(
                    trader,
                    feed,
                    start=datetime.combine(train[-1].date(), SESSION_CLOSE, IST),
                    end=datetime.combine(test[-1].date(), time(23, 59), IST),
                )
                in_sample = tuned.in_sample.summary()
                window = Window(
                    train_start=train[0].date(),
                    train_end=train[-1].date(),
                    test_start=test[0].date(),
                    test_end=test[-1].date(),
                    params=tuned.best_params,
                    in_sample_return_pct=in_sample["return_pct"],
                    in_sample_sharpe=in_sample["sharpe"],
                    forward_return_pct=round(_change(before, account.equity()), 4),
                    buy_hold_return_pct=round(
                        _change(
                            signals["Open"].loc[test[0]], signals["Close"].loc[test[-1]]
                        ),
                        4,
                    ),
                    trades=len(account.fills()) - fills_before,
                )
                results.append(window)
                if on_window:
                    on_window(window)

            fills = account.fills()
            curve = account.equity_curve()
            return WalkForwardReport(
                symbol=symbol,
                strategy=strategy_cls.__name__,
                windows=results,
                account=account.summary(),
                equity_curve=curve,
                buy_hold_return_pct=_change(
                    signals["Open"].loc[windows[0][1][0]],
                    signals["Close"].loc[windows[-1][1][-1]],
                ),
                max_drawdown_pct=_max_drawdown(curve),
                trades=len(fills),
                commissions=sum(fill.commission for fill in fills),
                account_path=str(path) if account_path else None,
            )
