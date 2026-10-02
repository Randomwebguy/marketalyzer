import numpy as np
import pytest
from backtesting import Backtest, Strategy

from marketalyzer.backtest.strategies import (
    STRATEGIES,
    RsiReversion,
    SmaCross,
    get_strategy,
    rsi,
    sma,
    strategy_params,
)


def test_sma():
    np.testing.assert_allclose(sma([1, 2, 3, 4], 2), [np.nan, 1.5, 2.5, 3.5])


def test_rsi_bounds_and_extremes():
    rising = rsi(np.arange(1, 40, dtype=float), 14)
    assert np.isnan(rising[:14]).all()
    assert rising[-1] == pytest.approx(100)
    falling = rsi(np.arange(40, 1, -1, dtype=float), 14)
    assert falling[-1] == pytest.approx(0)
    mixed = rsi(np.sin(np.arange(200) / 5) + 10, 14)
    assert np.nanmin(mixed) >= 0
    assert np.nanmax(mixed) <= 100


def test_get_strategy():
    assert get_strategy("sma_cross") is SmaCross
    assert get_strategy(RsiReversion) is RsiReversion
    with pytest.raises(ValueError, match="Available: sma_cross, rsi_reversion"):
        get_strategy("nope")


def test_strategy_params():
    assert strategy_params(SmaCross) == {"fast": 10, "slow": 50}
    assert strategy_params(RsiReversion) == {"period": 14, "lower": 30, "upper": 70}
    assert strategy_params(Strategy) == {}


@pytest.mark.parametrize("strategy", list(STRATEGIES.values()))
def test_strategies_trade_long_only(strategy, prices):
    params = {"fast": 5, "slow": 20} if strategy is SmaCross else {}
    stats = Backtest(prices, strategy, cash=100_000, finalize_trades=True).run(**params)
    trades = stats["_trades"]
    assert len(trades) > 0
    assert (trades["Size"] > 0).all()


@pytest.mark.parametrize("strategy", list(STRATEGIES.values()))
def test_default_grids_respect_constraints(strategy):
    params = strategy_params(strategy)
    assert set(strategy.param_grid) <= set(params)
    defaults = type("Params", (), params)
    assert strategy.constraint(defaults)
