import pickle

import pytest

from marketalyzer.backtest.costs import BistCosts, round_to_tick, tick_size


def test_commission_includes_bsmv():
    costs = BistCosts()
    assert costs(100, 50.0) == pytest.approx(5_000 * 0.002 * 1.05)
    assert costs(-100, 50.0) == costs(100, 50.0)
    assert costs.effective_rate == pytest.approx(0.0021)


def test_minimum_commission_applies_to_whole_share_orders():
    costs = BistCosts(min_commission=5.0)
    assert costs(1, 10.0) == pytest.approx(5.0 * 1.05)
    assert costs(10_000, 10.0) == pytest.approx(100_000 * 0.002 * 1.05)


def test_minimum_commission_ignored_while_sizing():
    costs = BistCosts(min_commission=5.0)
    assert costs(0.5, 10.0) == pytest.approx(0.5 * 10.0 * 0.002 * 1.05)


def test_exchange_fee_is_not_taxed():
    costs = BistCosts(commission_rate=0.0, exchange_fee_rate=0.0001)
    assert costs(1_000, 10.0) == pytest.approx(1.0)


def test_costs_survive_pickling_for_parallel_optimization():
    costs = BistCosts(commission_rate=0.001)
    assert pickle.loads(pickle.dumps(costs)) == costs  # noqa: S301


@pytest.mark.parametrize(
    ("price", "tick"),
    [
        (5.0, 0.01),
        (19.99, 0.01),
        (20.0, 0.02),
        (49.98, 0.02),
        (50.0, 0.05),
        (100.0, 0.10),
        (249.9, 0.10),
        (250.0, 0.25),
        (500.0, 0.50),
        (1_000.0, 1.0),
        (2_500.0, 2.5),
    ],
)
def test_tick_size(price, tick):
    assert tick_size(price) == tick


def test_tick_size_rejects_negative_prices():
    with pytest.raises(ValueError):
        tick_size(-1.0)


@pytest.mark.parametrize(
    ("price", "direction", "expected"),
    [
        (123.456, "nearest", 123.5),
        (123.456, "down", 123.4),
        (123.41, "up", 123.5),
        (20.03, "nearest", 20.04),
        (301.1, "nearest", 301.0),
        (301.2, "up", 301.25),
        (12.345, "down", 12.34),
    ],
)
def test_round_to_tick(price, direction, expected):
    assert round_to_tick(price, direction) == expected
