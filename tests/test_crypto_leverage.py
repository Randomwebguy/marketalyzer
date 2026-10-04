"""Leverage on the crypto rule: margin, funding, liquidation."""

import numpy as np
import pandas as pd
import pytest

from marketalyzer.crypto import leverage, neural

FREE = {"fee": 0.0, "slippage": 0.0, "funding": 0.0}


def market(prices, lows=None):
    index = pd.date_range("2025-01-01", periods=len(prices), freq="D")
    closes = pd.DataFrame({"A": prices}, index=index, dtype=float)
    return closes, pd.DataFrame({"A": lows or prices}, index=index, dtype=float)


def test_leverage_multiplies_the_move_of_the_margin():
    closes, lows = market([100, 110, 110])
    held = np.array([[1.0], [1.0], [0.0]])
    one = leverage.simulate(closes, lows, held, 2, leverage.Terms(1.0, **FREE))
    three = leverage.simulate(closes, lows, held, 2, leverage.Terms(3.0, **FREE))
    assert one.values[-1] == pytest.approx(1 + 0.5 * 0.10)
    assert three.values[-1] == pytest.approx(1 + 0.5 * 0.30)
    assert three.trades == 2 and not three.liquidations


def test_one_times_leverage_with_spot_costs_matches_the_spot_book():
    closes, lows = market([100, 104, 97, 120, 120])
    held = np.array([[0.5], [0.5], [0.5], [0.5], [0.0]])
    spot = neural.simulate(closes, held)
    terms = leverage.Terms(1.0, fee=neural.FEE, slippage=neural.SLIPPAGE, funding=0.0)
    perp = leverage.simulate(closes, lows, held, 2, terms)
    assert perp.values[-1] == pytest.approx(spot.values[-1], rel=1e-3)


def test_funding_eats_the_margin_every_day():
    closes, lows = market([100] * 11)
    held = np.ones((11, 1))
    paid = leverage.simulate(closes, lows, held, 1,
                             leverage.Terms(2.0, fee=0, slippage=0, funding=0.365))  # fmt: skip
    assert paid.funding == pytest.approx(10 * 2 * 0.001)
    assert paid.values[-1] == pytest.approx(1 - 0.02)


def test_a_deep_wick_liquidates_and_the_coin_waits_for_a_fresh_signal():
    closes, lows = market([100, 99, 101, 102, 103, 104], [100, 75, 101, 102, 103, 104])
    held = np.array([[1.0], [1.0], [1.0], [0.0], [1.0], [1.0]])
    out = leverage.simulate(closes, lows, held, 2, leverage.Terms(4.0, **FREE))
    assert out.liquidations == [("2025-01-02", "A")]
    assert out.values[1] == pytest.approx(0.5)  # the margin is gone
    assert out.values[2] == pytest.approx(0.5)  # not bought back while still "held"
    assert out.values[5] == pytest.approx(0.5 + 0.25 * 4 * (104 / 103 - 1))
    safe = leverage.simulate(closes, lows, held, 2, leverage.Terms(2.0, **FREE))
    assert not safe.liquidations
