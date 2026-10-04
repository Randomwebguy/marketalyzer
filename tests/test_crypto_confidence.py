"""The 15/30-minute confidence score used by scripts/crypto/score_study.py."""

import numpy as np
import pandas as pd

from marketalyzer.crypto import confidence


def bars(seed=0, n=400, drift=0.0, start="2026-10-01"):
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n, freq="15min")
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.003, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.001,
                         "Low": np.minimum(open_, close) * 0.999, "Close": close,
                         "Volume": rng.uniform(50, 150, n)}, index=index)  # fmt: skip


def test_scores_are_bounded_causal_and_read_the_trend():
    up = confidence.scores(bars(1, drift=0.002))
    down = confidence.scores(bars(1, drift=-0.002))
    assert up["long"].iloc[-50:].mean() > 60 > up["short"].iloc[-50:].mean()
    assert down["short"].iloc[-50:].mean() > 60 > down["long"].iloc[-50:].mean()
    full = confidence.scores(bars(2))
    cut = confidence.scores(bars(2).iloc[:301])
    assert np.allclose(full.iloc[:301].fillna(-1), cut.fillna(-1))
    valid = full["long"].dropna()
    assert valid.between(0, 100).all() and np.isnan(full["long"].iloc[0])
