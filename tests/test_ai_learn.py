"""The learning blind test: strategy context, the decision journal, lessons,
walk-forward windows and saved runs.

Prices come from the demo generator (synthetic, deterministic); the language
models are replaced by small rule-based fakes.
"""

import json
from datetime import timedelta

import pandas as pd

# Shared fakes; importing the fixtures makes them active here too.
from test_ai_lab import (  # noqa: F401
    DATE,
    END,
    STRATEGY,
    RuleModel,
    answer,
    answer_text,
    config,
    no_models,
    synthetic,
)

from marketalyzer import blind
from marketalyzer.ai import learn
from marketalyzer.ai.decide import Decider, DecisionCache

# --- Strategy context --------------------------------------------------------------


def test_track_record():
    trades = [{"return_pct": 4.0, "bars": 5}, {"return_pct": -2.0, "bars": 3}]
    assert learn.track_record(trades) == {
        "islem": 2,
        "kazancli_%": 50.0,
        "ort_getiri_%": 1.0,
        "ort_bar": 4.0,
    }
    assert learn.track_record([]) == {"islem": 0}


def test_strategy_brief_is_cached_and_falls_back(tmp_path):
    calls = []

    def complete(api_key, model, messages, **kwargs):
        calls.append(messages)
        return answer_text('{"tur": "dönüş", "ozet": "Düşüşte alır."}')

    cache = DecisionCache(tmp_path / "c.sqlite")
    decider = Decider("k", "m", cache=cache, complete=complete)
    brief = {"tur": "dönüş", "ozet": "Düşüşte alır."}
    assert learn.strategy_brief(decider, STRATEGY) == brief
    assert learn.strategy_brief(decider, STRATEGY) == brief
    assert len(calls) == 1
    assert calls[0][0]["content"] == learn.BRIEF_SYSTEM
    assert calls[0][1]["content"] == STRATEGY
    broken = Decider("k", "m", complete=lambda *a, **k: answer_text("yok"))
    assert learn.strategy_brief(broken, STRATEGY) == {"tur": "bilinmiyor", "ozet": ""}


def test_views_carry_strategy_context_from_before_the_test():
    short, long = RuleModel(), RuleModel()
    blind.run_blind(
        config(symbols=["THYAO"], end=END - timedelta(days=60)),
        Decider("k", "m", complete=short),
    )
    blind.run_blind(config(symbols=["THYAO"]), Decider("k", "m", complete=long))
    first = json.loads(short.views[0])
    assert first["strateji_ozeti"]["tur"] == "trend"
    assert first["strateji_gecmisi"]["islem"] > 0
    # The track record uses only bars before the test, so the end changes nothing.
    assert first["strateji_gecmisi"] == json.loads(long.views[0])["strateji_gecmisi"]


# --- Lockstep walk -----------------------------------------------------------------


def test_symbol_order_does_not_change_results():
    a = blind.run_blind(
        config(symbols=["THYAO", "GARAN"]), Decider("k", "m", complete=RuleModel())
    )
    b = blind.run_blind(
        config(symbols=["GARAN", "THYAO"]), Decider("k", "m", complete=RuleModel())
    )

    def key(d):
        return d["time"], d["symbol"]

    assert sorted(a["decisions"], key=key) == sorted(b["decisions"], key=key)
    assert a["ai"]["return_pct"] == b["ai"]["return_pct"]
    assert a["ai"]["trades"] == b["ai"]["trades"] > 0


def test_signals_only_run_matches_the_ai_following_every_signal():
    def follow(api_key, model, messages, **kwargs):
        if messages[0]["content"] == learn.BRIEF_SYSTEM:
            return answer_text('{"tur": "trend", "ozet": "Kesişimde alır."}')
        holding = json.loads(messages[1]["content"])["pozisyon"]["durum"] == "var"
        return answer_text(json.dumps({"karar": "SAT" if holding else "AL"}))

    result = blind.run_blind(config(), Decider("k", "m", complete=follow))
    assert result["ai"]["trades"] == result["signals"]["trades"]
    assert result["ai"]["return_pct"] == result["signals"]["return_pct"]


# --- Decision journal --------------------------------------------------------------


def record(label, action, confidence=60, event="giriş"):
    return {
        "symbol": "THYAO",
        "event": event,
        "label": label,
        "action": action,
        "confidence": confidence,
    }


def test_journal_hides_outcomes_until_they_resolve():
    day = pd.Timestamp
    journal = learn.Journal({"THYAO": "Hisse A"})
    missed = journal.add(record("BEKLE", "hold"), holding=False, features={})
    journal.resolve(missed, 9.9, "sinyal işlemi", day("2025-03-10"))
    assert journal.view(day("2025-03-07")) is None
    view = journal.view(day("2025-03-10"))
    assert view["ozet"] == {
        "reddedilen_giris": {"adet": 1, "kar_ettirecek_%": 100.0, "ort_%": 9.9}
    }
    assert view["son_sonuclar"] == [
        "Hisse A · giriş · BEKLE %60 → sinyal işlemi %+9.9 (kaçırıldı)"
    ]
    assert "THYAO" not in json.dumps(view)
    assert missed.record["outcome_pct"] == 9.9


def test_journal_summary_and_verdicts():
    day = pd.Timestamp("2025-03-10")
    journal = learn.Journal({"THYAO": "Hisse A"})
    cases = [
        (record("AL", "buy"), False, 4.0, "işlem", "doğru"),
        (record("AL", "buy"), False, -2.0, "işlem", "zarar"),
        (record("BEKLE", "hold"), False, -3.0, "sinyal işlemi", "doğru kaçınıldı"),
        (
            record("SAT", "sell", event="çıkış"),
            True,
            2.0,
            "10 bar sonra",
            "erken satış",
        ),
        (
            record("TUT", "hold", event="çıkış"),
            True,
            -1.0,
            "10 bar sonra",
            "tutmak zarar",
        ),
    ]
    for rec, holding, pct, kind, _ in cases:
        journal.resolve(journal.add(rec, holding=holding, features={}), pct, kind, day)
    view = journal.view(day)
    assert view["ozet"]["alinan_giris"] == {"adet": 2, "kazancli_%": 50.0, "ort_%": 1.0}
    assert view["ozet"]["pozisyon_kararlari"] == {"adet": 2, "dogru_%": 0.0}
    assert [line.rsplit("(", 1)[1][:-1] for line in view["son_sonuclar"]] == [
        verdict for *_, verdict in cases
    ]
    assert journal.fresh(day) == 5
    journal.mark(day)
    assert journal.fresh(day) == 0


def test_blind_journal_feeds_later_decisions_without_names():
    model = RuleModel()
    result = blind.run_blind(
        config(learning="journal"), Decider("k", "m", complete=model)
    )
    assert result["learning"]["mode"] == "journal"
    assert result["learning"]["resolved"] > 0
    texts = [v for v in model.views if "karar_gunlugu" in v]
    assert texts
    for text in texts:
        assert "THYAO" not in text and "GARAN" not in text and not DATE.search(text)
    assert any(d.get("outcome_kind") for d in result["decisions"])
    assert result["audit"]["blind"] is True


def test_rejected_entries_learn_from_the_signal_trade():
    def never(api_key, model, messages, **kwargs):
        if messages[0]["content"] == learn.BRIEF_SYSTEM:
            return answer_text('{"tur": "trend", "ozet": "Kesişimde alır."}')
        return answer("BEKLE")

    result = blind.run_blind(
        config(symbols=["THYAO"], learning="journal"),
        Decider("k", "m", complete=never),
    )
    returns = {t["return_pct"] for t in result["signal_trades"]}
    outcomes = [
        d for d in result["decisions"] if d.get("outcome_kind") == "sinyal işlemi"
    ]
    assert outcomes
    assert all(d["outcome_pct"] in returns for d in outcomes)


def test_learning_keeps_later_bars_from_changing_earlier_decisions():
    """The blindness property with the journal: extending the test changes no view."""
    short, long = RuleModel(), RuleModel()
    blind.run_blind(
        config(end=END - timedelta(days=60), learning="journal"),
        Decider("k", "m", complete=short),
    )
    blind.run_blind(config(learning="journal"), Decider("k", "m", complete=long))
    shared = len(short.views)
    assert any("karar_gunlugu" in v for v in short.views)
    assert sorted(long.views[:shared]) == sorted(short.views)
