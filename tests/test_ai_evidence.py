"""Evidence for entry decisions: readings, past signal trades, verified rules,
the daily and market context, the statistics filter and the decision comparison.

Prices come from the demo generator (synthetic, deterministic); the language
model is replaced by a small rule-based fake.
"""

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

# Serve demo bars instead of Yahoo; keep the model list off the network.
from test_ai_lab import RuleModel, config, frame_of, no_models, synthetic  # noqa: F401

from marketalyzer import blind, research
from marketalyzer.ai import evidence
from marketalyzer.ai.context import Context
from marketalyzer.ai.decide import Decider, EvidenceDecider

# --- Readings and samples ------------------------------------------------------------


def test_readings_never_look_ahead():
    frame = frame_of()
    full = research.indicators(frame)
    for cut in (260, 400, len(frame) - 3):
        part = research.indicators(frame.iloc[:cut])
        assert evidence.readings(part, -1) == evidence.readings(full, cut - 1)
    values = evidence.readings(full, 300, index_change=1.5)
    assert set(values) == set(evidence.FEATURES)
    assert values["supertrend_genel"] in (1.0, -1.0)
    assert values["goreli_guc"] == pytest.approx(values["getiri_20"] - 1.5, abs=1e-3)


def sample(pct, known, **values):
    return evidence.Sample(values, pct, pd.Timestamp(known), "X")


def pool_of(n=40):
    """RSI 0..n-1; the low half lost 2 %, the high half won 3 %."""
    return evidence.Pool([
        sample(-2.0 if k < n // 2 else 3.0, f"2025-01-{k % 28 + 1:02d}", rsi=float(k))
        for k in range(n)
    ])  # fmt: skip


def test_pool_counts_a_trade_only_once_it_closed():
    pool = pool_of()
    early = pool.known(pd.Timestamp("2025-01-05"))
    assert early and all(s.known <= pd.Timestamp("2025-01-05") for s in early)
    assert pool.similar({"rsi": 5.0}, pd.Timestamp("2024-12-31")) is None
    assert len(pool.known(pd.Timestamp("2025-02-01"))) == 40


def test_similar_signals_are_the_nearest_readings():
    found = pool_of().similar({"rsi": 2.0}, pd.Timestamp("2025-02-01"))
    assert found["tumu"] == {"adet": 40, "kazancli_%": 50.0, "ort_getiri_%": 0.5}
    assert found["benzer"]["adet"] == 13
    assert found["benzer"]["ort_getiri_%"] == -2.0
    high = pool_of().similar({"rsi": 38.0}, pd.Timestamp("2025-02-01"))
    assert high["benzer"]["ort_getiri_%"] == 3.0


# --- Rules ----------------------------------------------------------------------------


def test_rules_are_read_strictly():
    text = json.dumps({"kurallar": [
        {"kosullar": [["rsi", "<", 20], ["adx", ">=", "25"]], "eylem": "reddet"},
        {"kosullar": [{"ozellik": "atr", "islem": ">", "deger": 3}], "eylem": "Uygula"},
        {"kosullar": [["hisse", "<", 1]], "eylem": "reddet"},
        {"kosullar": [["rsi", "==", 1]], "eylem": "reddet"},
        {"kosullar": [["rsi", "<", 1]], "eylem": "kapat"},
        {"kosullar": [], "eylem": "reddet"},
    ]})  # fmt: skip
    rules = evidence.parse_rules("Kurallar: " + text)
    assert [r.text() for r in rules] == [
        "RSI < 20 ve ADX >= 25 ise reddet",
        "ATR % > 3 ise uygula",
    ]
    assert evidence.parse_rules("kural yok") == []


def test_a_rule_is_kept_only_if_past_signals_bear_it_out():
    samples = pool_of().samples
    low = evidence.Rule((("rsi", "<", 15.0),), "reddet")
    kept = evidence.verify(low, samples)
    assert kept["kabul"] and kept["adet"] == 15 and kept["ort_getiri_%"] == -2.0
    assert evidence.describe(kept).startswith("RSI < 15 ise reddet (15 geçmiş sinyal")
    wrong = evidence.verify(evidence.Rule((("rsi", "<", 15.0),), "uygula"), samples)
    assert not wrong["kabul"] and wrong["neden"] == "geçmiş sinyallerde tutmadı"
    few = evidence.verify(evidence.Rule((("rsi", "<", 5.0),), "reddet"), samples)
    assert not few["kabul"] and few["neden"].startswith("az örnek")
    assert low.matches({"rsi": 3.0}) and not low.matches({"rsi": None})


def test_slices_split_each_reading_in_thirds():
    parts = evidence.slices(pool_of().samples)["rsi"]
    assert [p["adet"] for p in parts] == [13, 13, 14]
    assert parts[0]["aralik"] == [0.0, 12.0] and parts[0]["ort_getiri_%"] == -2.0


# --- Context --------------------------------------------------------------------------


def daily(closes, start="2025-01-01"):
    index = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=index)


def test_context_is_cut_before_the_day_for_hourly_decisions():
    rising = daily(np.linspace(100, 200, 260))
    index = daily(np.linspace(1000, 1100, 260))
    day = rising.index[250]
    hourly = Context({"A": rising}, index, intraday=True)
    every_day = Context({"A": rising}, index, intraday=False)
    at = pd.Timestamp(day) + pd.Timedelta(hours=11)
    # The hourly view ends at the previous close plus the current price.
    previous = float(rising["Close"].iloc[249])
    seen = hourly.daily("A", at, previous * 1.1)
    assert seen["getiri_5_gun_%"] == pytest.approx(
        (previous * 1.1 / float(rising["Close"].iloc[245]) - 1) * 100, abs=0.01
    )
    assert every_day.daily("A", at, previous) is None
    before = hourly.market(at)["xu100_20_gun_%"]
    same_day = every_day.market(at)["xu100_20_gun_%"]
    closes = index["Close"].to_numpy()
    assert before == pytest.approx((closes[249] / closes[229] - 1) * 100, abs=0.01)
    assert same_day == pytest.approx((closes[250] / closes[230] - 1) * 100, abs=0.01)


def test_breadth_counts_stocks_above_their_average():
    up = daily(np.linspace(100, 200, 120))
    down = daily(np.linspace(200, 100, 120))
    stocks = {f"U{k}": up for k in range(3)} | {f"D{k}": down for k in range(3)}
    context = Context(stocks, None, intraday=False)
    assert context.market(up.index[-1]) == {"genislik_sma50_ustu_%": 50.0}
    few = Context({"U": up, "D": down}, None, intraday=False)
    assert few.market(up.index[-1]) is None


# --- The statistics filter --------------------------------------------------------------


def view(holding=False, event="giriş", average=None):
    found = {"benzer": {"adet": 20, "kazancli_%": 60.0, "ort_getiri_%": average}}
    key = "benzer_gecmis_cikislar" if holding else "benzer_gecmis_sinyaller"
    return {
        "strateji": {"olay": event},
        "pozisyon": {"durum": "var" if holding else "yok"},
        **({key: found} if average is not None else {}),
    }


def test_the_statistics_filter_follows_what_similar_signals_did():
    decide = EvidenceDecider().decide
    assert decide(view(average=1.2)).action == "buy"
    assert decide(view(average=-0.4)).action == "hold"
    assert decide(view()).action == "buy"  # no evidence yet: follow the signal
    assert decide(view(holding=True, event="çıkış")).action == "sell"
    kept = decide(view(holding=True, event="çıkış", average=2.0))
    assert kept.action == "hold" and kept.label == "TUT" and kept.confidence == 60
    assert decide(view(holding=True, event="çıkış", average=-1.0)).action == "sell"
    assert decide(view(holding=True, event="gözden geçirme")).action == "hold"


def test_exit_rules_are_told_apart():
    hold = evidence.Rule((("adx", ">", 30.0),), "tut")
    assert hold.exit and hold.text() == "çıkış sinyalinde ADX > 30 ise tut"
    assert not evidence.Rule((("adx", ">", 30.0),), "reddet").exit
    rule = evidence.parse_rules(
        '{"kurallar": [{"kosullar": [["adx", ">", 30]], "eylem": "Tut"}]}'
    )
    assert rule == [hold]
    up = [sample(2.0, "2025-01-02", adx=40.0) for _ in range(12)]
    down = [sample(-1.0, "2025-01-02", adx=10.0) for _ in range(12)]
    row = evidence.verify(hold, up + down)
    assert row["kabul"] and evidence.describe(row).startswith(
        "çıkış sinyalinde ADX > 30 ise tut (12 geçmiş çıkış: tutmak %100.0"
    )


# --- The blind test with evidence ------------------------------------------------------


@pytest.fixture
def stats_run():
    return blind.run_blind(config(mode="stats"))


def test_stats_mode_decides_without_a_model(stats_run):
    result = stats_run
    assert result["ai"]["decisions"] > 0 and result["ai"]["cost_usd"] == 0
    assert result["ai"]["model"]["id"] == "istatistik-filtresi"
    assert result["evidence"]["samples"] >= result["evidence"]["before_test"] > 0
    entries = [d for d in result["decisions"] if d["event"] == "giriş"]
    assert entries and all("readings" in d for d in entries)
    assert any("evidence" in d for d in entries)
    with pytest.raises(ValueError, match="öğrenme"):
        config(mode="stats", learning="journal").check()


def test_the_comparison_judges_each_entry_by_its_signal_trade(stats_run):
    c = stats_run["comparison"]
    assert c["taken"]["count"] + c["passed"]["count"] == c["entries"] > 0
    signal_pnl = sum(t["pnl"] for t in stats_run["signal_trades"])
    assert c["pnl"]["signals"] == pytest.approx(signal_pnl, abs=0.01)
    assert c["pnl"]["ai"] == pytest.approx(
        sum(t["pnl"] for t in stats_run["trades"]), abs=0.01
    )
    picked = c["pnl"]["taken"] + c["pnl"]["passed"]
    assert c["pnl"]["random"] == pytest.approx(
        picked / c["entries"] * c["taken"]["count"], abs=0.05
    )
    assert 0 <= c["accuracy_pct"] <= 100
    assert 0 <= c["exits"]["followed"] <= c["exits"]["signals"]
    kept = [
        d
        for d in stats_run["decisions"]
        if d["event"] == "çıkış" and d["label"] == "TUT"
    ]
    assert all(d["evidence"]["benzer"]["ort_getiri_%"] > 0 for d in kept)


def test_entry_views_carry_context_and_evidence():
    model = RuleModel()
    blind.run_blind(config(symbols=["THYAO"]), Decider("k", "m", complete=model))
    views = [json.loads(v) for v in model.views]
    entries = [v for v in views if v["strateji"]["olay"] == "giriş"]
    exits = [v for v in views if v["strateji"]["olay"] == "çıkış"]
    assert entries and exits and all("piyasa" in v for v in views)
    assert all("benzer_gecmis_sinyaller" in v for v in entries[1:])
    assert all("benzer_gecmis_cikislar" in v for v in exits[1:])
    assert not any("benzer_gecmis_sinyaller" in v for v in exits)
    assert all("gunluk" not in v for v in views)  # daily bars need no daily section


def test_evidence_only_counts_trades_closed_before_the_decision():
    config_ = config(symbols=["THYAO"], mode="stats")
    script = blind._script(config_.script, config_.source)
    data, benchmark = blind.load(config_, script)
    found = blind.gather_evidence(config_, script, data, benchmark)
    first = data[0].frame.index[data[0].first]
    known = found.pool.known(first)
    assert known and all(s.known <= first for s in known)
    assert len(found.pool.samples) > len(known)
    assert {s.code for s in found.pool.samples} > {"THYAO"}
    assert found.exits.samples and all(
        s.known <= first for s in found.exits.known(first)
    )


def test_a_test_ending_later_sees_the_same_evidence():
    short_end = date.today() - timedelta(days=90)
    short = blind.run_blind(config(symbols=["THYAO"], mode="stats", end=short_end))
    long = blind.run_blind(config(symbols=["THYAO"], mode="stats"))
    shared = len(short["decisions"])
    assert shared > 0
    for a, b in zip(short["decisions"], long["decisions"][:shared]):
        assert a.get("evidence") == b.get("evidence") and a["action"] == b["action"]
