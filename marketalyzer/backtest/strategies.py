"""Example long-only strategies for BIST backtests.

Each strategy exposes its tunable parameters as class attributes and a default
``param_grid`` for optimization, so the CLI or an AI agent can run and tune it by
name. Strategies must live at module level: optimization runs them in worker
processes.
"""

from typing import Any, ClassVar

import numpy as np
import pandas as pd
from backtesting import Strategy
from backtesting.lib import crossover


def sma(values: Any, period: int) -> np.ndarray:
    """Return the simple moving average."""
    return pd.Series(values).rolling(period).mean().to_numpy()


def rsi(values: Any, period: int = 14) -> np.ndarray:
    """Return the relative strength index, using Wilder's smoothing."""
    delta = pd.Series(values).diff()
    smoothing = {"alpha": 1 / period, "adjust": False, "min_periods": period}
    gain = delta.clip(lower=0).ewm(**smoothing).mean()
    loss = (-delta.clip(upper=0)).ewm(**smoothing).mean()
    return (100 - 100 / (1 + gain / loss)).to_numpy()


def _fast_below_slow(params: Any) -> bool:
    return params.fast < params.slow


def _lower_below_upper(params: Any) -> bool:
    return params.lower < params.upper


class SmaCross(Strategy):
    """Buy when the fast SMA crosses above the slow SMA; sell on the reverse cross."""

    fast = 10
    slow = 50

    param_grid: ClassVar[dict[str, list]] = {
        "fast": list(range(5, 35, 5)),
        "slow": list(range(20, 130, 10)),
    }
    constraint = staticmethod(_fast_below_slow)

    def init(self):
        """Compute the moving averages."""
        self.fast_sma = self.I(
            sma, self.data.Close, self.fast, name=f"SMA({self.fast})"
        )
        self.slow_sma = self.I(
            sma, self.data.Close, self.slow, name=f"SMA({self.slow})"
        )

    def next(self):
        """Trade on crossovers."""
        if not self.position and crossover(self.fast_sma, self.slow_sma):
            self.buy()
        elif self.position and crossover(self.slow_sma, self.fast_sma):
            self.position.close()


class RsiReversion(Strategy):
    """Buy when RSI falls below ``lower``; sell when it rises above ``upper``."""

    period = 14
    lower = 30
    upper = 70

    param_grid: ClassVar[dict[str, list]] = {
        "period": [7, 14, 21],
        "lower": [20, 25, 30, 35],
        "upper": [65, 70, 75, 80],
    }
    constraint = staticmethod(_lower_below_upper)

    def init(self):
        """Compute the RSI."""
        self.rsi = self.I(rsi, self.data.Close, self.period, name=f"RSI({self.period})")

    def next(self):
        """Trade on RSI thresholds."""
        if not self.position and self.rsi[-1] < self.lower:
            self.buy()
        elif self.position and self.rsi[-1] > self.upper:
            self.position.close()


STRATEGIES: dict[str, type[Strategy]] = {
    "sma_cross": SmaCross,
    "rsi_reversion": RsiReversion,
}


def get_strategy(strategy: str | type[Strategy]) -> type[Strategy]:
    """Resolve a strategy name from ``STRATEGIES`` or pass a class through."""
    if isinstance(strategy, type) and issubclass(strategy, Strategy):
        return strategy
    try:
        return STRATEGIES[strategy]
    except KeyError:
        raise ValueError(
            f"Unknown strategy {strategy!r}. Available: {', '.join(STRATEGIES)}."
        ) from None


def strategy_params(strategy: type[Strategy]) -> dict[str, Any]:
    """Return a strategy's tunable parameters and their default values."""
    return {
        name: getattr(strategy, name)
        for name in dir(strategy)
        if not name.startswith("_")
        and isinstance(getattr(strategy, name), (int, float))
        and not isinstance(getattr(strategy, name), bool)
    }
