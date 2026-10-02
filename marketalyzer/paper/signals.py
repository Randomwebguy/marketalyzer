"""Turn backtest strategies into live position targets."""

import warnings
from typing import Any

import pandas as pd
from backtesting import Backtest, Strategy

from marketalyzer.backtest.strategies import get_strategy


def wants_long(
    strategy: str | type[Strategy],
    data: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> bool:
    """Return whether a strategy would hold a long position after the last bar.

    The strategy runs over ``data`` exactly as in a backtest. Its position after
    the last bar, adjusted for any order it placed on that bar (which a backtest
    would fill at the next open), is the position it wants now. The same class
    therefore drives both backtests and paper trading.
    """
    strategy_cls = get_strategy(strategy)
    with warnings.catch_warnings():
        # Open trades at the end are the point here, not a problem.
        warnings.simplefilter("ignore")
        stats = Backtest(data, strategy_cls, cash=1e12).run(**(params or {}))
    instance = stats["_strategy"]
    long = instance.position.size > 0
    for order in instance.orders:
        if not order.is_contingent:
            long = order.size > 0
    return long
