"""Point-in-time universe and the candidate strategies, on synthetic data."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from marketalyzer.crypto import evaluate, strategies, universe


def candles(closes, start="2024-01-01", volume=5e6):
    index = pd.date_range(start, periods=len(closes), freq="D")
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1.0, "QuoteVolume": volume},
                        index=index)  # fmt: skip


# --- Universe -----------------------------------------------------------------------


def test_stablecoins_wrapped_coins_and_leveraged_tokens_are_left_out():
    pairs = ["BTCUSDT", "BTCUPUSDT", "BTCDOWNUSDT", "JUPUSDT", "USDCUSDT", "WBTCUSDT",
             "BNBBULLUSDT", "BNBUSDT", "EURUSDT"]  # fmt: skip
    assert universe.tradable(pairs) == ["BTCUSDT", "JUPUSDT", "BNBUSDT"]


def test_a_ticker_reused_after_a_gap_becomes_two_coins():
    old = candles(np.linspace(80, 0.01, 100), "2022-01-01")
    new = candles(np.linspace(5, 1, 50), "2022-05-31")
    parts = universe.segments("LUNAUSDT", pd.concat([old, new]))
    assert list(parts) == ["LUNA", "LUNA#2"]
    assert parts["LUNA"].index[-1] < parts["LUNA#2"].index[0]


def test_the_universe_needs_a_year_of_history_volume_and_a_live_market():
    coins = {
        "OLD": candles(np.ones(800), "2023-01-01", volume=9e6),
        "SMALL": candles(np.ones(800), "2023-01-01", volume=1e6),
        "YOUNG": candles(np.ones(200), "2024-12-01", volume=50e6),
        "DEAD": candles(np.ones(400), "2023-01-01", volume=80e6),  # ends 2024-02
        "MID": candles(np.ones(800), "2023-01-01", volume=5e6),
    }
    months = universe.monthly(coins, date(2025, 3, 1), date(2025, 4, 1), size=20)
    assert months[date(2025, 3, 1)] == ["OLD", "MID"]
    assert universe.monthly(coins, date(2025, 3, 1), date(2025, 3, 1), size=1)[
        date(2025, 3, 1)
    ] == ["OLD"]
    index = pd.date_range("2025-02-25", "2025-04-05", freq="D")
    member = universe.membership(months, index)
    assert not member.loc["2025-02-28"].any() and member.loc["2025-03-01", "OLD"]


# --- Data helpers -------------------------------------------------------------------


def test_prices_stop_at_a_coins_last_candle_and_it_is_sold_there():
    closes = strategies.table({"A": candles([1, 2, 3, 4, 5]), "B": candles([1, 2, 3])})
    assert np.isnan(closes["B"].iloc[3]) and closes["A"].iloc[4] == 5
    targets = strategies.exit_at_end(np.full((5, 2), 0.5), closes)
    assert targets[:, 1].tolist() == [0.5, 0.5, 0, 0, 0]
    assert targets[:, 0].tolist() == [0.5] * 5


# --- Signals ------------------------------------------------------------------------


def test_the_donchian_ensemble_rides_a_trend_and_leaves_it():
    close = np.r_[np.arange(1, 401), np.arange(400, 0, -1)].astype(float)
    signal = strategies.donchian(close)
    assert signal[399] == 1
    assert 0 < signal[420] < 1  # short lookbacks out, long ones still in
    assert signal[-1] == 0
    assert np.array_equal(strategies.donchian(close[:450]), signal[:450])  # causal


def test_the_rule_picks_the_universe_s_strongest_and_obeys_exits():
    index = pd.date_range("2025-01-01", "2025-09-30", freq="D")
    n = len(index)
    closes = pd.DataFrame({"A": np.linspace(100, 300, n), "B": np.linspace(100, 150, n),
                           "C": np.linspace(100, 900, n)}, index=index)  # fmt: skip
    members = pd.DataFrame({"A": True, "B": True, "C": False}, index=index)
    on = index.get_loc(pd.Timestamp("2025-04-02"))
    off = index.get_loc(pd.Timestamp("2025-05-10"))
    flags = {c: (np.zeros(n, bool), np.zeros(n, bool)) for c in closes}
    for c in closes:
        flags[c][0][on] = True
    flags["A"][1][off] = True
    held = strategies.rule_held(closes, members, flags, np.ones(n, bool),
                                date(2025, 4, 1), top=1, lookback=30)  # fmt: skip
    assert held[on].tolist() == [1, 0, 0]  # C is stronger but not in the universe
    assert held[off].tolist() == [0, 0, 0]
    every = strategies.rule_held(closes, members, flags, np.ones(n, bool),
                                 date(2025, 4, 1), top=None)  # fmt: skip
    assert every[on].tolist() == [1, 1, 0]


def test_weekly_momentum_keeps_a_holding_inside_the_buffer():
    index = pd.date_range("2025-01-01", periods=120, freq="D")
    rising = {c: np.linspace(100, 100 + k, 120) for k, c in zip((50, 40, 30), "ABC")}
    closes = pd.DataFrame(rising, index=index)
    closes.loc[index[70] :, "B"] = closes["B"].iloc[69] * np.linspace(1, 1.6, 50)
    members = pd.DataFrame(True, index=index, columns=list("ABC"))
    held = strategies.weekly_momentum(closes, members, np.ones(120, bool),
                                      date(2025, 2, 1), top=1, keep=2)  # fmt: skip
    first = index.get_loc(pd.Timestamp("2025-02-01"))
    assert held[first].tolist() == [1, 0, 0]
    assert held[-1].tolist() == [1, 0, 0]  # B overtook A, but A is still rank 2
    tight = strategies.weekly_momentum(closes, members, np.ones(120, bool),
                                       date(2025, 2, 1), top=1, keep=1)  # fmt: skip
    assert tight[-1].tolist() == [0, 1, 0]


# --- Sizing -------------------------------------------------------------------------


def test_risk_weights_favor_the_calmer_coin_and_meet_the_target():
    rng = np.random.default_rng(3)
    index = pd.date_range("2024-01-01", periods=300, freq="D")
    calm = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    wild = 100 * np.exp(np.cumsum(rng.normal(0, 0.04, 300)))
    closes = pd.DataFrame({"calm": calm, "wild": wild}, index=index)
    weights = strategies.risk_weights(np.ones((300, 2)), closes, target_vol=0.25)
    ratio = weights[-1, 0] / weights[-1, 1]
    assert 1.5 < ratio < 2.7
    logs = np.log(closes).diff().to_numpy()[-90:]
    vol = np.sqrt(weights[-1] @ np.cov(logs, rowvar=False) @ weights[-1] * 365)
    assert vol == pytest.approx(0.25, rel=1e-6)
    capped = strategies.risk_weights(np.ones((300, 2)), closes, target_vol=5.0)
    assert capped[-1].sum() == pytest.approx(1.0)


def test_crowding_flags_a_funding_spike_against_its_own_past_year():
    index = pd.date_range("2024-01-01", periods=500, freq="D")
    rate = pd.Series(0.0001, index=index)
    rate.iloc[::3] = 0.0002  # some normal variation
    rate.iloc[450:460] = 0.002  # longs pay ten times more for ten days
    funding = pd.DataFrame({"A": rate, "B": np.nan}, index=index)
    flags = strategies.crowded(funding, index)
    assert flags[455, 0] and not flags[300, 0] and not flags[:, 1].any()


# --- Overfitting --------------------------------------------------------------------


def test_overfitting_probability_tells_luck_from_skill():
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.01, (1600, 8))
    assert 0.2 < evaluate.overfit_probability(noise) < 0.8
    skill = noise.copy()
    skill[:, 0] += 0.004
    assert evaluate.overfit_probability(skill) < 0.05
