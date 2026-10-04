"""The leveraged long/short experiment: martingale ladder, liquidation, scores, runner."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from marketalyzer.crypto import confidence, levrun, martingale

FREE = {"fee": 0.0, "slippage": 0.0}
LONG = martingale.Config(side="long", **FREE)
SHORT = martingale.Config(side="short", **FREE)


def opened(config=LONG, step=0, atr=1.0, cash=10_000.0):
    book = martingale.Book(cash, cash, step=step)
    martingale.open_position(book, config, "AUSDT", 100.0, atr, 80.0, "t0")
    return book


# --- The ladder ---------------------------------------------------------------------


def test_losses_climb_the_ladder_and_a_recovering_win_resets_it():
    book = opened()
    p = book.position
    assert (p.leverage, p.margin, p.units, p.stop, p.take) == (2, 2000, 40, 99, 101.5)
    assert p.liquidation == pytest.approx(100 * (1 - 0.5 + 0.005))
    lost = martingale.check(book, LONG, 100, 100.5, 98.9, "t1")
    assert lost["reason"] == "zarar durdur" and lost["pnl"] == -40 and book.step == 1
    martingale.open_position(book, LONG, "AUSDT", 100.0, 1.0, 80.0, "t2")
    assert book.position.leverage == 4 and book.position.margin == 2000  # same margin
    martingale.check(book, LONG, 100, 100.2, 98.0, "t3")
    martingale.open_position(book, LONG, "AUSDT", 100.0, 1.0, 80.0, "t4")
    won = martingale.check(book, LONG, 100, 101.6, 99.5, "t5")
    assert won["pnl"] == 8 * 2000 / 100 * 1.5 and won["cycle"] == "kazanıldı"
    assert book.step == 0 and book.cycle_margin is None and book.cycle_pnl == 0
    assert book.wallet == 10_000 - 40 - 80 + 240 and book.stats["cycles_won"] == 1


def test_a_loss_on_the_last_step_ends_the_cycle_and_a_small_win_keeps_the_step():
    book = martingale.Book(10_000.0, 10_000.0)
    for _ in range(4):
        martingale.open_position(book, LONG, "AUSDT", 100.0, 1.0, 80.0, "t")
        last = martingale.check(book, LONG, 100, 100, 98.0, "t")
    assert last["cycle"] == "kaybedildi" and book.step == 0
    assert book.stats["cycles_lost"] == 1 and book.stats["worst_cycle"] == -(
        40 + 80 + 160 + 200
    )
    martingale.open_position(book, LONG, "AUSDT", 100.0, 1.0, 80.0, "t")
    martingale.check(book, LONG, 100, 100, 98.0, "t")  # -: step 1
    martingale.open_position(book, LONG, "AUSDT", 100.0, 1.0, 80.0, "t")
    small = martingale.close(
        book, LONG, 100.25, "güven düştü", "t"
    )  # +20, cycle still -
    assert small["cycle"] == "sürüyor" and book.step == 1


def test_the_stop_comes_before_liquidation_unless_the_bar_gaps_past_it():
    book = opened(step=3)  # 10x: liquidation at 90.5, stop at 99
    stopped = martingale.check(book, LONG, 100, 100, 85, "t")
    assert stopped["reason"] == "zarar durdur" and stopped["pnl"] == -200
    book = opened(step=3)
    gone = martingale.check(book, LONG, 88, 89, 85, "t")  # opens below liquidation
    assert gone["reason"] == "tasfiye" and gone["pnl"] == -2000
    assert book.stats["liquidations"] == 1
    wide = opened(step=3, atr=15)  # stop at 85, beyond the liquidation price
    assert martingale.check(wide, LONG, 100, 100, 89, "t")["reason"] == "tasfiye"
    gap = opened()
    assert martingale.check(gap, LONG, 98, 98.5, 97, "t")["pnl"] == 40 * (98 - 100)


def test_the_short_account_mirrors_and_receives_positive_funding():
    book = opened(SHORT)
    p = book.position
    assert p.stop == 101 and p.take == 98.5 and p.liquidation == pytest.approx(149.5)
    paid = martingale.fund(book, SHORT, 0.0001, 100.0)
    assert paid == pytest.approx(-40 * 100 * 0.0001)  # shorts are paid
    won = martingale.check(book, SHORT, 100, 100.4, 98.4, "t")
    assert won["reason"] == "kâr al" and won["pnl"] == pytest.approx(60 + 0.4)
    long_book = opened()
    assert martingale.fund(long_book, LONG, 0.0001, 100.0) == pytest.approx(0.4)


def test_fees_and_slippage_are_charged_on_the_position_value():
    config = martingale.Config(side="long")
    book = opened(config)
    p = book.position
    assert p.entry == pytest.approx(100 * 1.0002) and p.fees == pytest.approx(
        4000 * 0.0005
    )
    trade = martingale.close(book, config, p.entry, "test", "t")
    assert trade["pnl"] == pytest.approx(-(p.fees + p.units * p.entry * 0.9998 * 0.0005)
                                         - p.units * p.entry * 0.0002, abs=0.005)  # fmt: skip


# --- The score ----------------------------------------------------------------------


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


# --- The runner ---------------------------------------------------------------------


class FakeMarket(levrun.Market):
    """Two coins: one rising steadily, one falling."""

    def __init__(self):
        self.frames = {"UPUSDT": bars(3, 600, 0.002, "2026-09-28"),
                       "DNUSDT": bars(4, 600, -0.002, "2026-09-28")}  # fmt: skip

    def bars(self, symbol, interval, start=None, limit=1000, now=None):
        frame = self.frames[symbol]
        if interval == "1m":  # each 15-minute bar as fifteen flat minutes
            frame = frame.resample("1min").ffill()
        ends = frame.index + pd.Timedelta(interval.replace("m", "min"))
        keep = ends <= pd.Timestamp(now.replace(tzinfo=None))
        if start is not None:
            keep &= frame.index >= pd.Timestamp(start.replace(tzinfo=None))
        return frame[keep].iloc[-limit:] if start is None else frame[keep].iloc[:limit]

    def active(self):
        return list(self.frames)

    def prices(self):
        return {s: float(f["Close"].iloc[-1]) for s, f in self.frames.items()}

    def funding(self, symbol):
        return 0.0001


def test_the_runner_opens_the_best_coin_for_each_side_and_tracks_it():
    levrun.main(["init"])
    market = FakeMarket()
    now = datetime(2026, 10, 3, 12, 0, 5, tzinfo=timezone.utc)
    report = levrun.step(now, market)
    assert (
        report["long"]["opened"] == "UPUSDT" and report["short"]["opened"] == "DNUSDT"
    )
    again = levrun.step(
        now + timedelta(seconds=30), market
    )  # same bar: no new decision
    assert "opened" not in again["long"]
    later = now
    for _ in range(40):  # ten hours, minute checks and decisions every quarter
        later += timedelta(minutes=15)
        levrun.step(later, market)
    long_state = levrun.status("long")
    assert long_state["stats"]["trades"] >= 1 and long_state["equity"]
    assert (
        long_state["scores"]["UPUSDT"]["long"] > long_state["scores"]["UPUSDT"]["short"]
    )
    assert set(long_state["scores"]["UPUSDT"]["parts"]["long"]) == set(
        confidence.WEIGHTS
    )
    assert levrun.status("short")["side"] == "short"


def test_the_web_shows_both_accounts_at_live_prices(monkeypatch):
    from fastapi.testclient import TestClient

    from marketalyzer.web.app import create_app

    app = create_app("test-token")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-token"
        assert client.get("/api/leverage").json() == {"exists": False, "accounts": []}
        levrun.main(["init"])
        market = FakeMarket()
        levrun.step(datetime(2026, 10, 3, 12, 0, 5, tzinfo=timezone.utc), market)
        monkeypatch.setattr(levrun.binance, "futures_prices", lambda: {"UPUSDT": 999.0})
        data = client.get("/api/leverage").json()
        long_side, short_side = data["accounts"]
        assert (
            data["exists"]
            and long_side["side"] == "long"
            and short_side["side"] == "short"
        )
        assert (
            long_side["position"]["price"] == 999.0 and long_side["position"]["pnl"] > 0
        )
        assert long_side["value"] > long_side["wallet"]


def test_replay_runs_both_sides_over_past_bars():
    market = FakeMarket()
    now = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
    result = levrun.replay(days=3, now=now, market=market)
    assert (
        result["long"]["stats"]["trades"] > 0 and result["short"]["stats"]["trades"] > 0
    )
    assert result["long"]["end"] > 0 and result["bars"] > 200
