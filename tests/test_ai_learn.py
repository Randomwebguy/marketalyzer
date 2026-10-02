"""The learning blind test: strategy context, the decision journal, lessons,
walk-forward windows and saved runs.

Prices come from the demo generator (synthetic, deterministic); the language
models are replaced by small rule-based fakes.
"""

import json
from datetime import timedelta

# Shared fakes; importing the fixtures makes them active here too.
from test_ai_lab import (  # noqa: F401
    END,
    STRATEGY,
    RuleModel,
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
