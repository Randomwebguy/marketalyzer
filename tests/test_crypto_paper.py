"""The crypto paper account: fractional USD ledger, the rule and the hourly runner.

Prices are synthetic; signals are set by hand where a test needs a flip.
"""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from marketalyzer.crypto import momentum, runner
from marketalyzer.crypto.ledger import CryptoLedger
from marketalyzer.web.app import create_app

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
TOKEN = "test-token"


def daily(code_index, days=600, end=date(2026, 10, 4)):
    """A random walk with a drift that grows with the coin's index (deterministic)."""
    rng = np.random.default_rng(code_index)
    index = pd.date_range(end - timedelta(days=days - 1), end, freq="D")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004 * code_index, 0.02, len(index))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.01,
                         "Low": np.minimum(open_, close) * 0.99, "Close": close,
                         "Volume": 1e6}, index=index)  # fmt: skip


FRAMES = {code: daily(k) for k, code in enumerate(momentum.UNIVERSE)}


def fake_load(code, start, end, interval, **kwargs):
    frame = FRAMES[code]
    if interval == "1h":  # one hourly bar at today's open price
        last = frame.iloc[-1]
        hourly = pd.DataFrame([last.values], columns=frame.columns,
                              index=[pd.Timestamp(NOW.date()) + pd.Timedelta(hours=9)])  # fmt: skip
        return hourly, None
    days = frame.index.normalize()
    return frame[(days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))], None


@pytest.fixture
def ledger(tmp_path):
    with CryptoLedger.create(tmp_path / "paper.sqlite", 100_000, now=NOW) as account:
        yield account


# --- Ledger ---------------------------------------------------------------------------


def test_the_ledger_buys_fractions_and_charges_fee_and_slippage(ledger):
    fill = ledger.buy("BTC-USD", 20_000, 84_000.0, now=NOW, reason="test")
    price = 84_000 * 1.0005
    assert fill.price == pytest.approx(price)
    assert fill.units == pytest.approx(20_000 / (price * 1.001))
    assert 0 < fill.units < 1 and ledger.cash == pytest.approx(80_000)
    assert ledger.value({"BTC-USD": 84_000.0}) == pytest.approx(
        80_000 + fill.units * 84_000
    )
    sold = ledger.sell("BTC-USD", 88_000.0, now=NOW)
    proceeds = fill.units * 88_000 * 0.9995 * 0.999
    assert ledger.cash == pytest.approx(80_000 + proceeds)
    assert sold.pnl == pytest.approx(proceeds - 20_000)
    assert ledger.sell("BTC-USD", 88_000.0) is None and not ledger.positions()
    big = ledger.buy("ETH-USD", 10**9, 4_000.0, now=NOW)  # capped at the cash
    assert ledger.cash == pytest.approx(0, abs=1e-6) and big.units > 0
    ledger.put("selection", ["ETH-USD"])
    assert ledger.get("selection") == ["ETH-USD"] and ledger.get("nope", 5) == 5
    ledger.record({"ETH-USD": 4_000.0}, NOW)
    assert ledger.equity_curve()[-1][1] == pytest.approx(
        ledger.value({"ETH-USD": 4_000.0})
    )


def test_a_second_ledger_cannot_overwrite_the_first(tmp_path, ledger):
    with pytest.raises(FileExistsError):
        CryptoLedger.create(ledger.path)
    with pytest.raises(FileNotFoundError):
        CryptoLedger(tmp_path / "none.sqlite")


# --- The rule -----------------------------------------------------------------------


def test_selection_ranks_the_last_90_days_before_the_quarter():
    start = date(2026, 10, 1)
    index = pd.date_range("2025-01-01", "2026-10-03", freq="D")
    rising = pd.Series(np.linspace(100, 300, len(index)), index=index)
    flat = pd.Series(100.0, index=index)
    young = pd.Series(np.linspace(100, 900, 200), index=index[-200:])
    after = rising.copy()
    after[after.index >= pd.Timestamp(start)] = 10_000.0  # after the start: not seen
    picks = momentum.select({"A": rising, "B": flat, "C": young, "D": after * 0.5},
                            start, momentum.Rule(top=2))  # fmt: skip
    assert picks == ["A", "D"]  # C is too young; D's jump came after the start
    assert momentum.quarter_start(date(2026, 11, 15)) == date(2026, 10, 1)


def test_the_supertrend_signal_is_causal():
    frame = FRAMES["ETH-USD"]
    full = momentum.signals(frame)
    cut = momentum.signals(frame.iloc[:400])
    assert all(np.array_equal(a[:400], b) for a, b in zip(full, cut, strict=True))


# --- The runner ---------------------------------------------------------------------


def flags_with(entries_on=None, exits_on=None):
    """Signals that flip only on the given (code, day) pairs."""

    def signals(frame, rule=momentum.Rule()):
        days = frame.index.normalize()
        entries = np.zeros(len(frame), dtype=bool)
        exits = np.zeros(len(frame), dtype=bool)
        last = frame.index[-1]
        code = next(
            c
            for c, f in FRAMES.items()
            if last in f.index and f["Close"].loc[last] == frame["Close"].iloc[-1]
        )
        for c, day in entries_on or []:
            entries |= (c == code) & (days == pd.Timestamp(day))
        for c, day in exits_on or []:
            exits |= (c == code) & (days == pd.Timestamp(day))
        return entries, exits

    return signals


def test_the_runner_buys_on_an_entry_in_btc_uptrend_and_sells_on_an_exit(
    ledger, monkeypatch
):
    first = runner.step(ledger, NOW, fake_load)
    chosen = first["selection"]
    assert len(chosen) == 5 and first["days"] == ["2026-10-03"]
    pick = chosen[0]
    monkeypatch.setattr(
        momentum, "btc_up", lambda close, rule=None: pd.Series(True, index=close.index)
    )
    monkeypatch.setattr(
        momentum, "signals", flags_with(entries_on=[(pick, "2026-10-04")])
    )
    FRAMES_NEXT = NOW + timedelta(days=1)
    for code in FRAMES:  # one more finished day
        FRAMES[code] = daily(
            momentum.UNIVERSE.index(code), days=601, end=date(2026, 10, 5)
        )
    try:
        bought = runner.step(ledger, FRAMES_NEXT, fake_load)
        assert [f["side"] for f in bought["fills"]] == ["buy"] and bought["fills"][0][
            "symbol"
        ] == pick
        assert pick in ledger.positions()
        assert bought["fills"][0]["units"] * bought["fills"][0][
            "price"
        ] == pytest.approx(100_000 / 5, rel=0.01)
        again = runner.step(ledger, FRAMES_NEXT, fake_load)  # same hour: nothing new
        assert again["days"] == [] and again["fills"] == []
        monkeypatch.setattr(
            momentum, "signals", flags_with(exits_on=[(pick, "2026-10-05")])
        )
        for code in FRAMES:
            FRAMES[code] = daily(
                momentum.UNIVERSE.index(code), days=602, end=date(2026, 10, 6)
            )
        sold = runner.step(ledger, NOW + timedelta(days=2), fake_load)
        assert [f["side"] for f in sold["fills"]] == [
            "sell"
        ] and pick not in ledger.positions()
    finally:
        for code in FRAMES:
            FRAMES[code] = daily(momentum.UNIVERSE.index(code))


def test_no_buy_while_btc_is_below_its_average(ledger, monkeypatch):
    monkeypatch.setattr(
        momentum, "btc_up", lambda close, rule=None: pd.Series(False, index=close.index)
    )
    runner.step(ledger, NOW - timedelta(days=1), fake_load)
    pick = ledger.get("selection")[0]
    monkeypatch.setattr(
        momentum, "signals", flags_with(entries_on=[(pick, "2026-10-03")])
    )
    report = runner.step(ledger, NOW, fake_load)
    assert report["days"] == ["2026-10-03"] and report["fills"] == []


def test_a_new_quarter_sells_the_coins_that_dropped_out(ledger, monkeypatch):
    ledger.put("quarter", "2026-07-01")
    ledger.put("selection", ["XLM-USD"])
    ledger.put("last_day", "2026-10-02")
    ledger.buy("XLM-USD", 10_000, 0.3, now=NOW)
    monkeypatch.setattr(
        momentum, "select", lambda closes, start, rule=None: ["BTC-USD"]
    )
    monkeypatch.setattr(momentum, "signals", flags_with())  # no flips
    report = runner.step(ledger, NOW, fake_load)
    assert report["selected"] == ["BTC-USD"]
    assert [(f["symbol"], f["side"], f["reason"]) for f in report["fills"]] == [
        ("XLM-USD", "sell", "seçimden düştü")
    ]
    assert ledger.get("quarter") == "2026-10-01"


def test_missed_days_are_caught_up_in_order(ledger):
    ledger.put("last_day", "2026-09-30")
    report = runner.step(ledger, NOW, fake_load)
    assert report["days"] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert ledger.get("last_day") == "2026-10-03"


# --- Web ----------------------------------------------------------------------------


def test_each_account_keeps_its_own_rule_and_one_step_serves_all(monkeypatch):
    with runner.open_account("paper", 100_000, now=NOW) as main:
        assert runner.rule_of(main) == momentum.Rule()
    runner.open_account("top3", 50_000, top=3, now=NOW).close()
    with pytest.raises(FileExistsError):
        runner.open_account("top3")
    with pytest.raises(ValueError):
        runner.ledger_path("../evil")
    assert runner.accounts() == ["paper", "top3"]
    calls = []

    def counting(code, *args, **kwargs):
        calls.append(code)
        return fake_load(code, *args, **kwargs)

    reports = runner.step_all(NOW, counting)
    assert len(reports["paper"]["selection"]) == 5
    assert reports["top3"]["selection"] == reports["paper"]["selection"][:3]
    assert calls.count("ETH-USD") == 2  # daily once, hourly once, for both accounts
    with CryptoLedger(runner.ledger_path("top3")) as ledger:
        assert runner.status(ledger)["label"] == "En güçlü 3"


def test_the_web_shows_the_crypto_accounts(monkeypatch):
    app = create_app(TOKEN)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        assert client.get("/api/crypto").json() == {"exists": False, "accounts": []}
        assert client.post("/api/crypto/step").status_code == 404
        runner.open_account("paper", 50_000, now=NOW).close()
        runner.open_account("top3", 50_000, top=3, now=NOW).close()
        monkeypatch.setattr(runner.services, "load_bars", fake_load)
        monkeypatch.setattr(
            runner,
            "datetime",
            type("Clock", (), {"now": staticmethod(lambda tz=None: NOW)}),
        )
        stepped = client.post("/api/crypto/step").json()
        assert stepped["paper"]["days"] == ["2026-10-03"]
        assert len(stepped["top3"]["selection"]) == 3
        state = client.get("/api/crypto").json()
        main, second = state["accounts"]
        assert state["exists"] and main["account"] == "paper"
        assert main["initial"] == 50_000 and main["rule"]["top"] == 5
        assert main["value"] == pytest.approx(50_000, rel=0.01)
        assert main["equity"] and main["selection"] == stepped["paper"]["selection"]
        assert second["label"] == "En güçlü 3" and second["rule"]["top"] == 3
