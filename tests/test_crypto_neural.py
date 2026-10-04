"""The NeuralEngine: shadow books, Hedge weights per market state, causality."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from marketalyzer.crypto import momentum, neural


def frame(seed, days=900, end=date(2026, 10, 3), drift=0.0):
    rng = np.random.default_rng(seed)
    index = pd.date_range(end - timedelta(days=days - 1), end, freq="D")
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.03, len(index))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.01,
                         "Low": np.minimum(open_, close) * 0.99, "Close": close,
                         "Volume": 1e6}, index=index)  # fmt: skip


FRAMES = {
    code: frame(k, drift=0.0003 * k) for k, code in enumerate(momentum.UNIVERSE[:6])
}
START = date(2025, 1, 1)


def test_a_book_pays_fee_and_slippage_like_the_ledger():
    closes = pd.DataFrame({"A": [100.0, 110.0, 110.0]})
    targets = np.array([[0.5], [0.5], [0.0]])
    book = neural.simulate(closes, targets)
    units = 0.5 / (100 * 1.0005 * 1.001)
    assert book.values[0] == pytest.approx(0.5 + units * 100)
    assert book.weights[1, 0] == pytest.approx(units * 110 / book.values[1])
    assert book.values[2] == pytest.approx(0.5 + units * 110 * 0.9995 * 0.999)
    assert book.weights[2, 0] == 0 and book.traded == pytest.approx(0.5 + units * 110)


def test_a_book_rebalances_only_outside_the_band():
    closes = pd.DataFrame({"A": [100.0, 104.0, 130.0]})
    targets = np.full((3, 1), 0.5)
    book = neural.simulate(closes, targets, band=0.05)
    assert book.weights[1, 0] > 0.5  # +4 %: inside the band, left alone
    assert book.weights[2, 0] == pytest.approx(0.5, abs=1e-3)  # +30 %: trimmed back
    never = neural.simulate(closes, targets)
    assert never.weights[2, 0] > 0.55


def test_the_engine_moves_weight_to_the_expert_that_earns_in_each_state():
    rows = 400
    states = np.tile([0, 1], rows // 2)
    rewards = np.zeros((rows, 3))
    rewards[1:, 0] = np.where(states[:-1] == 0, 0.01, -0.01)  # wins after state 0
    rewards[1:, 1] = np.where(states[:-1] == 1, 0.01, -0.01)  # wins after state 1
    settings = neural.Settings(eta=5.0, floor=0.06, forget=0.0)
    weights = neural.learn(rewards, states, settings)
    assert weights[-2].argmax() == 0 and states[-2] == 0
    assert weights[-1].argmax() == 1 and states[-1] == 1
    assert weights.min() >= 0.06 / 3 - 1e-12
    assert np.allclose(weights.sum(axis=1), 1)
    assert np.allclose(weights[0], 1 / 3)  # nothing learned yet


def test_forgetting_lets_the_engine_change_its_mind():
    rewards = np.zeros((300, 2))
    rewards[1:150, 0] = 0.01
    rewards[150:, 1] = 0.01
    states = np.zeros(300, int)
    sticky = neural.learn(rewards, states, neural.Settings(eta=3.0, forget=0.0))
    fading = neural.learn(rewards, states, neural.Settings(eta=3.0, forget=0.05))
    assert sticky[200, 1] < 0.5 < fading[200, 1]


def test_rotation_holds_the_strongest_and_cash_while_btc_is_down():
    index = pd.date_range("2025-01-01", "2025-06-30", freq="D")
    closes = pd.DataFrame({"A": np.linspace(100, 200, len(index)),
                           "B": np.linspace(100, 110, len(index)),
                           "C": np.linspace(100, 50, len(index))}, index=index)  # fmt: skip
    up = np.ones(len(index), bool)
    target = neural.rotation_targets(closes, up, date(2025, 4, 1), top=2,
                                     lookback=40, skip=10)  # fmt: skip
    april = index.get_loc(pd.Timestamp("2025-04-15"))
    assert target[april].tolist() == [0.5, 0.5, 0]
    assert target[index.get_loc(pd.Timestamp("2025-03-15"))].sum() == 0  # before start
    up[index >= pd.Timestamp("2025-05-31")] = False
    later = neural.rotation_targets(closes, up, date(2025, 4, 1), 2, 40, 10)
    assert later[index.get_loc(pd.Timestamp("2025-06-10"))].sum() == 0


def test_the_experts_and_the_engine_do_not_look_ahead():
    cut = pd.Timestamp("2026-03-31")
    closes, experts = neural.experts_of(FRAMES, START)
    short = {code: f[f.index <= cut] for code, f in FRAMES.items()}
    closes_cut, experts_cut = neural.experts_of(short, START)
    n = len(closes_cut)
    assert closes.index[n - 1] == cut
    for name, target in experts.items():
        assert np.array_equal(target[:n], experts_cut[name]), name
    first = int(np.searchsorted(closes.index, pd.Timestamp(START)))
    settings = neural.Settings(context="btc_breadth")
    book, weights, _ = neural.run(closes, experts, settings, first)
    book_cut, weights_cut, _ = neural.run(closes_cut, experts_cut, settings, first)
    assert np.allclose(book.values[:n], book_cut.values)
    assert np.allclose(weights[:n], weights_cut)
    assert np.all(book.values[:first] == 1) and book.values[-1] != 1
