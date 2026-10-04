"""Honest backtest judging: period results and the deflated Sharpe ratio."""

import numpy as np
import pandas as pd
import pytest

from marketalyzer.crypto import evaluate


def test_measure_rebases_on_the_close_before_the_period():
    index = pd.date_range("2023-12-30", periods=5, freq="D")
    values = np.array([2.0, 4.0, 5.0, 4.0, 6.0])  # 12-30 .. 01-03
    out = evaluate.measure(values, index, 2, 5)  # 2024-01-01 .. 01-03, base 12-31
    assert out["money"] == 150_000
    assert out["drawdown"] == pytest.approx(-20.0)
    assert out["years"] == {2024: 50.0}


def test_more_trials_need_a_higher_sharpe_to_be_believed():
    rng = np.random.default_rng(1)
    returns = rng.normal(0.002, 0.02, 1000)  # a per-day Sharpe near 0.1
    trials = rng.normal(0.0, 0.03, 200)
    one = evaluate.deflated_sharpe(returns, np.array([evaluate.sharpe(returns)]))
    many = evaluate.deflated_sharpe(returns, np.r_[trials, evaluate.sharpe(returns)])
    assert 0.5 < one <= 1 and many < one
    noise = rng.normal(0.0, 0.02, 1000)
    assert evaluate.deflated_sharpe(noise, np.r_[trials, evaluate.sharpe(noise)]) < 0.2
