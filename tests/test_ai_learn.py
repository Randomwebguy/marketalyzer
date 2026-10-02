"""The learning blind test: strategy context, the decision journal, lessons,
walk-forward windows and saved runs.

Prices come from the demo generator (synthetic, deterministic); the language
models are replaced by small rule-based fakes.
"""

import json
from datetime import date, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# Shared fakes; importing the fixtures makes them active here too.
from test_ai_lab import (  # noqa: F401
    DATE,
    END,
    START,
    STRATEGY,
    TOKEN,
    RuleModel,
    answer,
    answer_text,
    config,
    no_models,
    sse_events,
    synthetic,
)

from marketalyzer import blind, services
from marketalyzer.ai import decide, learn, runs
from marketalyzer.ai.decide import Decider, DecisionCache
from marketalyzer.ai.openrouter import OpenRouterError
from marketalyzer.ai.settings import save_settings
from marketalyzer.scripting import save_script
from marketalyzer.web.app import create_app

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
        calls.append((messages, kwargs))
        return answer_text('{"tur": "ortalamaya dönüş", "ozet": "Düşüşte alır."}')

    cache = DecisionCache(tmp_path / "c.sqlite")
    decider = Decider("k", "m", cache=cache, json_mode=True, complete=complete)
    brief = {"tur": "dönüş", "ozet": "Düşüşte alır."}
    assert learn.strategy_brief(decider, STRATEGY) == brief
    assert learn.strategy_brief(decider, STRATEGY) == brief
    assert len(calls) == 1
    messages, kwargs = calls[0]
    assert messages[0]["content"] == learn.BRIEF_SYSTEM
    assert messages[1]["content"] == STRATEGY
    # The decision model's settings and room for reasoning before the answer.
    assert kwargs["extra"]["response_format"] == {"type": "json_object"}
    assert kwargs["max_tokens"] == learn.BRIEF_TOKENS
    broken = Decider("k", "m", complete=lambda *a, **k: answer_text("yok"))
    assert learn.strategy_brief(broken, STRATEGY) == {"tur": "bilinmiyor", "ozet": ""}


def test_brief_types_are_read_leniently():
    def brief(kind):
        return learn._parse_brief(json.dumps({"tur": kind, "ozet": "x"}))["tur"]

    assert brief("trend takibi") == "trend"
    assert brief("trend ve kırılım") == "karma"
    assert brief("momentum") == "karma"
    assert brief("") == "bilinmiyor"


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


def test_lessons_drop_tickers_dates_and_extras():
    items = [
        "  RSI<30 iken sinyali uygula ",
        "THYAO'da dikkatli ol",
        "2025-01-02 sonrası temkinli ol",
        "x" * 400,
        *"abcdef",
    ]
    assert learn.clean_lessons(items, ["THYAO"]) == [
        "RSI<30 iken sinyali uygula",
        "a",
        "b",
        "c",
        "d",
    ]
    assert learn.parse_lessons('{"dersler": ["bir", "iki"]}') == ["bir", "iki"]
    assert learn.parse_lessons("Dersler:\n- bir\n• iki\n3. üç") == ["bir", "iki", "üç"]


def test_lessons_are_written_every_six_outcomes_and_reach_the_view():
    model = RuleModel()
    prompts = []

    def coach_complete(api_key, model_id, messages, **kwargs):
        prompts.append(messages)
        return answer_text('{"dersler": ["Aşırı satımda sinyali uygula."]}')

    events = []
    # Reviews every 5 bars give enough decisions for a few reflections.
    result = blind.run_blind(
        config(learning="journal", review_every=5),
        Decider("k", "m", complete=model),
        coach=learn.Coach("k", "koç", complete=coach_complete),
        emit=events.append,
    )
    assert prompts and prompts[0][0]["content"] == learn.REFLECT_SYSTEM
    payload = json.loads(prompts[0][1]["content"])
    assert len(payload["kararlar"]) >= learn.REFLECT_EVERY
    for messages in prompts:
        text = messages[1]["content"]
        assert "THYAO" not in text and "GARAN" not in text and not DATE.search(text)
    lessons = [e for e in events if e["type"] == "lesson"]
    assert lessons and lessons[0]["lessons"] == ["Aşırı satımda sinyali uygula."]
    assert any("Aşırı satımda sinyali uygula." in v for v in model.views)
    assert result["learning"]["lessons"] == ["Aşırı satımda sinyali uygula."]
    assert result["learning"]["history"][0]["resolved"] >= learn.REFLECT_EVERY
    assert result["learning"]["cost_usd"] > 0


def test_a_failing_coach_keeps_the_test_going():
    def broken(*args, **kwargs):
        raise OpenRouterError("koç yok", 500)

    result = blind.run_blind(
        config(learning="journal"),
        Decider("k", "m", complete=RuleModel()),
        coach=learn.Coach("k", "koç", complete=broken),
    )
    assert result["learning"]["lessons"] == []
    assert result["ai"]["decisions"] > 0


def run_result(script, lessons=None, ai_return=1.5):
    return {
        "period": {"start": "2025-10-02", "end": "2026-10-01"},
        "config": {"symbols": ["THYAO"], "script": script, "mode": "ai"},
        "ai": {"return_pct": ai_return, "trades": 3, "strategy": {"x": 1}},
        "signals": {"return_pct": 2.0, "trades": 5},
        "hold": {"return_pct": 9.0},
        "learning": {"mode": "journal", "lessons": lessons or []},
    }


def test_runs_are_saved_listed_and_loaded():
    first = runs.save(run_result("kesisim", ["Eski ders."]))
    second = runs.save(run_result("baska", ["Başka ders."], ai_return=3.0))
    listed = runs.list_runs()
    assert [row["id"] for row in listed] == [second, first]
    assert listed[0]["ai_return_pct"] == 3.0 and listed[0]["scripts"] == ["baska"]
    assert runs.load(first)["config"]["script"] == "kesisim"
    latest = runs.latest_learning("kesisim")
    assert latest == {"run_id": first, "lessons": ["Eski ders."]}
    assert runs.latest_learning("yok") is None
    for bad in ("../x", "20260101_000000_zzzzzz", ""):
        with pytest.raises(ValueError, match="Geçersiz"):
            runs.load(bad)
    with pytest.raises(ValueError, match="bulunamadı"):
        runs.load("20200101_000000_000000")


def test_web_saves_ai_runs_and_live_decisions_use_their_lessons(monkeypatch):
    save_settings(api_key="sk-or-v1-0123456789abcdef")
    save_script("kesisim", STRATEGY)
    model = RuleModel()
    original = decide.make_decider

    def make(api_key, choice, **kwargs):
        return original(api_key, choice, **kwargs, complete=model)

    monkeypatch.setattr(decide, "make_decider", make)
    app = create_app(TOKEN)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        request = {
            "symbols": ["THYAO"],
            "start": START.isoformat(),
            "end": END.isoformat(),
            "script": "kesisim",
            "years": 1,
            "use_cache": False,
            "learning": "journal",
        }
        events = sse_events(client.post("/api/lab/blind", json=request))
        (result,) = [e["result"] for e in events if e["type"] == "result"]
        assert result["learning"]["mode"] == "journal"
        run_id = result["run_id"]
        listed = client.get("/api/lab/runs").json()["runs"]
        assert listed[0]["id"] == run_id
        assert client.get(f"/api/lab/runs/{run_id}").json()["run_id"] == run_id
        assert client.get("/api/lab/runs/..%2Fx").status_code in (400, 404)
        runs.save(run_result("kesisim", ["Aşırı satımda sinyali uygula."]))
        live = client.post(
            "/api/lab/live",
            json={"symbols": ["THYAO"], "script": "kesisim", "years": 1},
        ).json()
        assert live["lessons"] == ["Aşırı satımda sinyali uygula."]
        assert "Aşırı satımda sinyali uygula." in model.views[-1]


# --- Walk-forward windows ----------------------------------------------------------

FASTER = STRATEGY.replace('fast = input.int(10, "Hızlı"', 'fast = input.int(5, "Hızlı"')


def test_windows_split_the_test_dates_evenly():
    cfg = config()
    data, _ = blind.load(cfg, services._script(None, cfg.source))
    parts = blind.windows(data, 3)
    dates = sorted(set().union(*(set(d.frame.index[d.first :]) for d in data)))
    assert len(parts) == 3
    assert parts[0][0] == dates[0] and parts[-1][1] == dates[-1]
    assert all(a[1] < b[0] for a, b in zip(parts, parts[1:]))
    sizes = [sum(start <= t <= end for t in dates) for start, end in parts]
    assert max(sizes) - min(sizes) <= 1
    assert blind.windows(data, 1) == [(dates[0], dates[-1])]


def test_rounds_need_round_learning():
    with pytest.raises(ValueError, match="Tur"):
        config(rounds=2).check()
    with pytest.raises(ValueError, match="Tur"):
        config(learning="rounds", rounds=5).check()


def test_one_round_matches_the_plain_run():
    plain = blind.run_blind(config(mode="signals"))
    one = blind.run_blind(config(mode="signals", learning="rounds", rounds=1))
    assert one["signals"] == plain["signals"]
    assert one["signal_trades"] == plain["signal_trades"]
    assert "rounds" not in one


def test_rounds_close_at_window_end_and_compound():
    result = blind.run_blind(config(mode="signals", learning="rounds", rounds=3))
    rows = result["rounds"]
    assert [row["round"] for row in rows] == [1, 2, 3]
    assert rows[0]["end"] < rows[1]["start"] and rows[1]["end"] < rows[2]["start"]
    reasons = [t["exit_reason"] for t in result["signal_trades"]]
    assert "tur sonu" in reasons
    still_open = [t for t in result["signal_trades"] if t.get("open")]
    assert len(still_open) <= 2
    assert all(t["exit_reason"] == "dönem sonu (açık)" for t in still_open)
    # Each window starts from the cash the previous one ended with.
    equity = [p["v"] for p in result["signals"]["equity"]]
    assert equity[0] > 0 and result["signals"]["equity_final"] == pytest.approx(
        equity[-1], abs=0.01
    )
    assert result["initial_signals"]["trades"] > 0


def test_revision_trades_only_the_next_window():
    seen = []

    def revise(round_no, script, report, cutoff):
        seen.append((round_no, cutoff, report["round"], script.name))
        new = services._script(None, FASTER)
        new.name = "ai_x_t2"
        return new, {"ok": True, "name": "ai_x_t2", "explanation": "- daha hızlı"}

    result = blind.run_blind(
        config(learning="rounds", rounds=2),
        Decider("k", "m", complete=RuleModel()),
        revise=revise,
    )
    rows = result["rounds"]
    assert seen == [(1, date.fromisoformat(rows[0]["end"][:10]), 1, rows[0]["script"])]
    assert rows[1]["script"] == "ai_x_t2"
    assert rows[0]["revision"]["name"] == "ai_x_t2"
    for decision in result["decisions"]:
        window = rows[decision["round"] - 1]
        assert window["start"] <= decision["time"] <= window["end"]


def test_failed_revision_keeps_the_script():
    result = blind.run_blind(
        config(learning="rounds", rounds=2),
        Decider("k", "m", complete=RuleModel()),
        revise=lambda *args: None,
    )
    rows = result["rounds"]
    assert rows[1]["script"] == rows[0]["script"]
    assert rows[0]["revision"]["ok"] is False


def test_web_learning_rounds(monkeypatch):
    save_settings(api_key="sk-or-v1-0123456789abcdef")
    model = RuleModel()
    original = decide.make_decider

    def make(api_key, choice, **kwargs):
        return original(api_key, choice, **kwargs, complete=model)

    monkeypatch.setattr(decide, "make_decider", make)
    monkeypatch.setattr(
        "marketalyzer.web.app.resolve_model", lambda key, chosen: "koç/m"
    )
    reflections = []

    def coach_complete(api_key, model_id, messages, **kwargs):
        reflections.append(model_id)
        return answer_text('{"dersler": ["Sinyali uygula."]}')

    def coach_stream(api_key, model_id, messages, **kwargs):
        yield {"type": "text", "text": f"```pine\n{FASTER}```\n- Daha hızlı."}
        yield {
            "type": "usage",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cost": 0.01,
        }

    real = learn.Coach
    monkeypatch.setattr(
        learn,
        "Coach",
        lambda key, model_id: real(
            key, model_id, complete=coach_complete, stream=coach_stream
        ),
    )
    request = {
        "symbols": ["THYAO", "GARAN"],
        "start": START.isoformat(),
        "end": END.isoformat(),
        "source": STRATEGY,
        "years": 1,
        "use_cache": False,
        "review_every": 5,
        "learning": "rounds",
        "rounds": 2,
    }
    app = create_app(TOKEN)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        estimate = client.post("/api/lab/estimate", json=request).json()
        assert estimate["learning"]["revisions"] == 1
        assert estimate["learning"]["coach_model"] == "koç/m"
        assert estimate["learning"]["reflections"] >= 1
        events = sse_events(client.post("/api/lab/blind", json=request))
        kinds = {event["type"] for event in events}
        assert {"strategy", "round", "round_end", "revision"} <= kinds
        (result,) = [e["result"] for e in events if e["type"] == "result"]
    rows = result["rounds"]
    assert [row["round"] for row in rows] == [1, 2]
    assert rows[0]["revision"]["ok"] is True
    assert rows[1]["script"] == "editor_t2"
    assert reflections and set(reflections) == {"koç/m"}
    assert result["learning"]["mode"] == "rounds" and result["run_id"]


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
