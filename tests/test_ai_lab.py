"""Signal research, fast decisions, the AI script author and the blind backtest.

Prices come from the demo generator (synthetic, deterministic); the language
model is replaced by small rule-based fakes.
"""

import json
import re
import threading
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from marketalyzer import blind, research, services
from marketalyzer.ai import author, decide, learn, openrouter
from marketalyzer.ai.decide import Decider, DecisionCache, parse_decision, resolve_model
from marketalyzer.ai.jobs import JobError, JobRunner
from marketalyzer.ai.openrouter import OpenRouterError
from marketalyzer.ai.settings import save_settings
from marketalyzer.scripting import get_script
from marketalyzer.web import demo
from marketalyzer.web.app import create_app
from openbb_bist.utils import yahoo

TOKEN = "test-token"
TODAY = services.today()
START = TODAY - timedelta(days=240)
END = TODAY - timedelta(days=30)
DATE = re.compile(r"\b(19|20)\d\d-\d\d-\d\d\b")
STRATEGY = """//@version=5
strategy("Kesişim", overlay=true)
fast = input.int(10, "Hızlı", minval=5, maxval=20, step=5)
slow = input.int(30, "Yavaş", minval=20, maxval=60, step=10)
f = ta.sma(close, fast)
s = ta.sma(close, slow)
plot(f, "Hızlı")
if ta.crossover(f, s)
    strategy.entry("Al", strategy.long)
    strategy.exit("Stop", "Al", stop=close * 0.9)
if ta.crossunder(f, s)
    strategy.close("Al")
"""


@pytest.fixture(autouse=True)
def synthetic(monkeypatch):
    """Serve the demo generator's bars instead of calling Yahoo."""

    async def get_chart(symbol, params):
        return demo.chart_payload(symbol, params)

    monkeypatch.setattr(yahoo, "get_chart", get_chart)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MARKETALYZER_DECISION_MODEL", raising=False)


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    """Keep tests off the network: the model list is never available."""

    def fail(*args, **kwargs):
        raise OpenRouterError("liste yok")

    monkeypatch.setattr(decide, "list_models", fail)
    monkeypatch.setattr("marketalyzer.web.app.list_models", fail)


def answer_text(text):
    return {
        "text": text,
        "model": "fake/model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
    }


def answer(karar, guven=70, gerekce="test"):
    return answer_text(json.dumps({"karar": karar, "guven": guven, "gerekce": gerekce}))


def is_brief(messages):
    """Tell the one-off strategy summary request from a decision."""
    return messages[0]["content"] == learn.BRIEF_SYSTEM


def brief_answer():
    return answer_text(
        '{"tur": "trend", "ozet": "Hızlı ortalama yavaşı kesince alır."}'
    )


class RuleModel:
    """Buys when flat and the supertrend is up; sells when it turns down."""

    def __init__(self):
        self.views = []
        self.lock = threading.Lock()

    def __call__(self, api_key, model, messages, **kwargs):
        if is_brief(messages):
            return brief_answer()
        view = json.loads(messages[1]["content"])
        with self.lock:
            self.views.append(messages[1]["content"])
        holding = view["pozisyon"]["durum"] == "var"
        up = view["trend"]["supertrend"] == "yukarı"
        if holding:
            return answer("TUT" if up else "SAT")
        return answer("AL" if up else "BEKLE")


def config(**changes):
    values = {
        "symbols": ["THYAO", "GARAN"],
        "start": START,
        "end": END,
        "source": STRATEGY,
        "years": 1.0,
    }
    values.update(changes)
    return blind.BlindConfig(**values)


# --- Research ----------------------------------------------------------------------


def frame_of(code="THYAO", days=700):
    frame, _ = services.load_bars(code, TODAY - timedelta(days=days), TODAY)
    return frame


def test_signals_never_look_ahead():
    frame = frame_of()
    full = research.signal_masks(research.indicators(frame))
    for cut in (250, 320, len(frame) - 7):
        part = research.signal_masks(research.indicators(frame.iloc[:cut]))
        for key, mask in part.items():
            assert np.array_equal(mask, full[key][:cut]), key


def test_forward_returns_start_at_the_next_open():
    ind = {
        "open": np.array([10.0, 11.0, 12.0, 13.0]),
        "close": np.array([10.5, 11.5, 12.5, 13.5]),
    }
    returns = research.forward_returns(ind, 2)
    assert returns[0] == pytest.approx(12.5 / 11.0 - 1)
    assert returns[1] == pytest.approx(13.5 / 12.0 - 1)
    assert np.isnan(returns[2:]).all()


def test_study_reads_only_up_to_the_cutoff():
    cutoff = TODAY - timedelta(days=100)
    result = research.study(["THYAO", "GARAN"], cutoff, years=1)
    assert result["cutoff"] == cutoff.isoformat()
    for item in result["symbols"]:
        assert item["end"] <= cutoff.isoformat()
        assert item["start"] >= (cutoff - timedelta(days=366)).isoformat()
        assert len(item["signals"]) == len(research.SIGNALS)
        assert item["behavior"]["liquidity"]["bucket"]
    assert len(result["ranking"]) == len(research.SIGNALS)
    assert result["correlation"]["symbols"] == ["THYAO", "GARAN"]
    assert research.DEPTH_NOTE in result["notes"]


def test_anonymous_study_hides_names_and_dates():
    result = research.study(["THYAO", "GARAN"], TODAY - timedelta(days=10), years=1)
    hidden = research.anonymize(result)
    text = json.dumps(hidden, ensure_ascii=False) + research.summary_text(hidden)
    assert "THYAO" not in text and "GARAN" not in text
    assert not DATE.search(text)
    assert [s["symbol"] for s in hidden["symbols"]] == ["Hisse A", "Hisse B"]


def test_study_limits():
    with pytest.raises(ValueError, match="En fazla"):
        research.study([f"S{i}" for i in range(9)])
    with pytest.raises(ValueError, match="bugünden sonra"):
        research.study(["THYAO"], TODAY + timedelta(days=3))
    with pytest.raises(ValueError, match="En az bir"):
        research.study([" "])


# --- Decisions ---------------------------------------------------------------------


def test_presets_resolve_to_the_newest_model():
    models = [
        {"id": "anthropic/claude-3.5-haiku"},
        {"id": "anthropic/claude-haiku-4.5"},
        {"id": "google/gemini-2.5-flash"},
        {"id": "google/gemini-3.5-flash-lite"},
        {"id": "google/gemini-3-flash-preview-11-2026"},
    ]
    assert resolve_model(None, models)[0] == "anthropic/claude-haiku-4.5"
    assert resolve_model("gemini-flash", models)[0] == "google/gemini-2.5-flash"
    assert (
        resolve_model("gemini-flash-lite", models)[0] == "google/gemini-3.5-flash-lite"
    )
    assert resolve_model("gpt-mini", models) == (
        "openai/gpt-5.4-mini",
        decide.PRESET_BY_KEY["gpt-mini"],
    )
    assert resolve_model("me/custom-1", models) == ("me/custom-1", None)


def test_parse_decision():
    fenced = (
        '```json\n{"karar": "al", "guven": "72.4", "gerekce": " Trend  güçlü "}\n```'
    )
    decision = parse_decision(fenced, holding=False)
    assert (decision.action, decision.label, decision.confidence, decision.reason) == (
        "buy",
        "AL",
        72,
        "Trend güçlü",
    )
    # An action that does not fit the position means "do nothing".
    assert parse_decision('{"karar": "AL"}', holding=True).label == "TUT"
    assert parse_decision('{"karar": "SAT"}', holding=False).label == "BEKLE"
    assert (
        parse_decision('{"karar": "SAT", "guven": 400}', holding=True).confidence == 100
    )
    with pytest.raises(ValueError):
        parse_decision("bilmiyorum", holding=False)
    with pytest.raises(ValueError, match="Geçersiz karar"):
        parse_decision('{"karar": "BELKİ"}', holding=False)


def test_decider_caches_answers(tmp_path):
    calls = []

    def complete(*args, **kwargs):
        calls.append(kwargs)
        return answer("AL")

    cache = DecisionCache(tmp_path / "decisions.sqlite")
    decider = Decider("k", "fake/model", cache=cache, complete=complete)
    view = {"pozisyon": {"durum": "yok"}, "x": 1}
    first = decider.decide(view)
    second = decider.decide(view)
    assert (first.label, first.cached, second.label, second.cached) == (
        "AL",
        False,
        "AL",
        True,
    )
    assert len(calls) == 1
    assert calls[0]["extra"]["provider"] == {"sort": "latency"}
    assert calls[0]["temperature"] == 0


def test_decision_cache_creates_its_folder_and_never_blocks(tmp_path):
    cache = DecisionCache(tmp_path / "new" / "deeper" / "decisions.sqlite")
    cache.put("a", {"text": "x"})
    assert cache.get("a") == {"text": "x"}
    broken = DecisionCache(tmp_path)  # a directory cannot be a database
    broken.put("a", {"text": "x"})
    assert broken.get("a") is None


def test_decider_retries_plainly_when_extras_are_rejected():
    calls = []
    preset = decide.PRESET_BY_KEY["gemini-flash"]

    def complete(api_key, model, messages, **kwargs):
        calls.append(kwargs["extra"])
        if "reasoning" in kwargs["extra"]:
            raise OpenRouterError("bad", 400)
        return answer("BEKLE")

    decider = Decider("k", "fake/strict", preset, json_mode=True, complete=complete)
    assert decider.decide({"pozisyon": {"durum": "yok"}}).label == "BEKLE"
    assert "response_format" in calls[0] and "reasoning" in calls[0]
    assert calls[1] == {"provider": {"sort": "latency"}}
    decider.decide({"pozisyon": {"durum": "yok"}, "y": 2})
    assert calls[2] == {"provider": {"sort": "latency"}}


def test_decider_errors_become_hold():
    def complete(*args, **kwargs):
        raise OpenRouterError("OpenRouter yanıt vermedi.")

    decision = Decider("k", "m", complete=complete).decide(
        {"pozisyon": {"durum": "var"}}
    )
    assert (decision.action, decision.label) == ("hold", "TUT")
    assert decision.error == "OpenRouter yanıt vermedi."


def test_snapshot_hides_the_price_level():
    frame = frame_of("ASELS", 500)
    index, _ = services.load_bars("XU100", TODAY - timedelta(days=500), TODAY)
    view = decide.snapshot(frame, benchmark=index)
    text = json.dumps(view, ensure_ascii=False)
    assert "ASELS" not in text and not DATE.search(text)
    assert view["son_20_kapanis_100_tabanli"][0] == 100.0
    assert "endeks" in view
    last = float(frame["Close"].iloc[-1])
    assert f"{last:.2f}" not in text


def test_speed_test(no_models):
    def complete(api_key, model, messages, **kwargs):
        if model.startswith("qwen"):
            raise OpenRouterError("yok", 404)
        return answer("AL")

    rows = decide.speed_test(
        "k", ["claude-haiku", "qwen-flash"], rounds=2, complete=complete
    )
    assert [r["key"] for r in rows] == ["claude-haiku", "qwen-flash"]
    assert rows[0]["ok"] and rows[0]["decision"] == "AL" and rows[0]["average_ms"] >= 0
    assert not rows[1]["ok"] and rows[1]["error"] == "yok"


# --- Blind backtest ----------------------------------------------------------------


def test_blind_signals_mode_needs_no_model():
    result = blind.run_blind(config(mode="signals"))
    assert "ai" not in result
    assert result["signals"]["trades"] == len(result["signal_trades"]) > 0
    assert result["hold"]["trades"] == 0
    assert result["period"]["start"] >= START.isoformat()
    assert result["period"]["end"] <= END.isoformat()
    assert [s["symbol"] for s in result["symbols"]] == ["THYAO", "GARAN"]


def test_blind_ai_decides_on_the_past_and_fills_next_open():
    model = RuleModel()
    result = blind.run_blind(config(), Decider("k", "fake/model", complete=model))
    ai = result["ai"]
    assert ai["decisions"] == len(result["decisions"]) == len(model.views) > 0
    assert result["audit"]["blind"] is True
    assert result["audit"]["rechecks"] == ai["decisions"]
    for decision in result["decisions"]:
        assert decision["data_end"] == decision["time"]
    buys = {
        (d["symbol"], d["time"]) for d in result["decisions"] if d["action"] == "buy"
    }
    for trade in result["trades"]:
        earlier = [
            t for s, t in buys if s == trade["symbol"] and t < trade["entry_time"]
        ]
        assert earlier, trade
    for text in model.views:
        assert "THYAO" not in text and "GARAN" not in text and not DATE.search(text)


def test_later_bars_do_not_change_earlier_decisions():
    """The blindness property: extending the test cannot change the past."""
    short_model, long_model = RuleModel(), RuleModel()
    short_end = END - timedelta(days=60)
    short = blind.run_blind(
        config(symbols=["THYAO"], end=short_end),
        Decider("k", "m", complete=short_model),
    )
    long = blind.run_blind(
        config(symbols=["THYAO"]), Decider("k", "m", complete=long_model)
    )
    # The short run's last bar has no next open, so it makes no decision there.
    shared = len(short_model.views)
    assert shared > 0
    assert long_model.views[:shared] == short_model.views
    assert long["decisions"][:shared] == short["decisions"]


def test_review_every_asks_while_holding():
    model = RuleModel()
    plain = blind.run_blind(
        config(symbols=["GARAN"]), Decider("k", "m", complete=model)
    )
    reviewed = blind.run_blind(
        config(symbols=["GARAN"], review_every=5),
        Decider("k", "m", complete=RuleModel()),
    )
    events = {d["event"] for d in reviewed["decisions"]}
    assert "gözden geçirme" in events
    assert reviewed["ai"]["decisions"] > plain["ai"]["decisions"]


def test_stop_loss_closes_trades():
    def always(api_key, model, messages, **kwargs):
        if is_brief(messages):
            return brief_answer()
        holding = json.loads(messages[1]["content"])["pozisyon"]["durum"] == "var"
        return answer("TUT" if holding else "AL")

    result = blind.run_blind(
        config(stop_loss_pct=1.0), Decider("k", "m", complete=always)
    )
    reasons = {t["exit_reason"] for t in result["trades"]}
    assert any(reason.startswith("zarar durdur") for reason in reasons)


def test_blind_config_checks():
    with pytest.raises(ValueError, match="bitişten önce"):
        config(start=END, end=START).check()
    with pytest.raises(ValueError, match="bugünden sonra"):
        config(end=TODAY + timedelta(days=5)).check()
    with pytest.raises(ValueError, match="Gözden geçirme"):
        config(review_every=7).check()
    with pytest.raises(ValueError, match="karar modeli"):
        blind.run_blind(config())


def test_indicator_script_is_rejected():
    source = '//@version=5\nindicator("RSI")\nplot(ta.rsi(close, 14), "RSI")\n'
    with pytest.raises(ValueError, match="strateji değil"):
        blind.run_blind(config(source=source, mode="signals"))


def test_estimate_counts_signal_events():
    cfg = config()
    script = services._script(None, cfg.source)
    data, _ = blind.load(cfg, script)
    plan = blind.estimate(cfg, data)
    assert plan["decisions"] == sum(s["entries"] + s["exits"] for s in plan["symbols"])
    assert plan["decisions"] > 0


def test_decide_now_uses_holdings():
    model = RuleModel()
    script = services._script(None, STRATEGY)
    rows = blind.decide_now(
        ["THYAO", "GARAN"],
        script,
        Decider("k", "m", complete=model),
        holdings={"GARAN": {"qty": 10, "avg_cost": 100.0, "since": None}},
        years=1,
    )
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["GARAN"]["held"] == 10
    assert by_symbol["GARAN"]["label"] in ("SAT", "TUT")
    assert by_symbol["THYAO"]["label"] in ("AL", "BEKLE")
    assert '"durum":"var"' in "".join(model.views)


# --- Script author -----------------------------------------------------------------


def test_extract_code():
    assert author.extract_code("x\n```pine\nplot(close)\n```\nok") == "plot(close)\n"
    assert author.extract_code("```\nplot(close)\n```") == "plot(close)\n"
    assert (
        author.extract_code("//@version=5\nplot(close)")
        == "//@version=5\nplot(close)\n"
    )
    assert author.extract_code("kod yok") is None
    assert author.explanation("```pine\na\n```\n- neden") == "- neden"


def stream_replies(*replies):
    calls = []

    def stream(api_key, model, messages, **kwargs):
        calls.append([dict(m) for m in messages])
        text = replies[len(calls) - 1]
        yield {"type": "text", "text": text}
        yield {
            "type": "usage",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost": 0.01,
        }

    return stream, calls


def test_author_fixes_and_saves_the_script():
    broken = '```pine\n//@version=5\nstrategy("x")\nif close >\n```'
    good = f"```pine\n{STRATEGY}```\n- Kesişim kullanıldı."
    stream, calls = stream_replies(broken, good)
    events = []
    result = author.author_script(
        ["THYAO", "GARAN"],
        cutoff=(TODAY - timedelta(days=200)).isoformat(),
        years=1,
        api_key="k",
        model="m",
        emit=events.append,
        stream=stream,
        name="ai_test",
    )
    assert result["name"] == "ai_test" and result["attempts"] == 2
    assert result["explanation"] == "- Kesişim kullanıldı."
    assert result["usage"]["cost"] == pytest.approx(0.02)
    assert get_script("ai_test")["source"] == STRATEGY
    reviews = [e for e in events if e["type"] == "review"]
    assert reviews[0]["problem"].startswith("Derleme hatası")
    assert reviews[1]["problem"] is None
    # The model saw letters and no dates; the fix request quotes the problem.
    prompt = calls[0][1]["content"]
    assert "Hisse A" in prompt and "THYAO" not in prompt and not DATE.search(prompt)
    assert "Derleme hatası" in calls[1][-1]["content"]


REPORT = {
    "round": 1,
    "bars": 60,
    "ai": {"return_pct": 1.2, "trades": 1, "win_rate_pct": 100.0, "exposure_pct": 4.0},
    "signals": {"return_pct": 3.4, "trades": 6, "win_rate_pct": 50.0},
    "hold_return_pct": 12.5,
    "benchmark_return_pct": 5.1,
    "symbols": [
        {
            "hisse": "Hisse A",
            "ai_trades": 1,
            "ai_return_pct": 1.2,
            "signals_trades": 6,
            "signals_return_pct": 3.4,
            "hold_return_pct": 12.5,
        }
    ],
    "karar_gunlugu": {"reddedilen_giris": {"adet": 5, "kar_ettirecek_%": 60.0}},
    "dersler": ["Aşırı satımda sinyali uygula."],
}


def test_window_report_text_is_anonymous():
    text = blind.report_text(REPORT)
    assert "Hisse A" in text and "Aşırı satımda sinyali uygula." in text
    assert "%12.5" in text and "XU100" in text and not DATE.search(text)


def test_author_revises_a_script_from_a_window_report():
    broken = '```pine\n//@version=5\nstrategy("x")\nif close >\n```'
    good = f"```pine\n{STRATEGY}```\n- Daha uzun tutuldu."
    stream, calls = stream_replies(broken, good)
    events = []
    result = author.revise_script(
        STRATEGY,
        blind.report_text(REPORT),
        ["THYAO", "GARAN"],
        cutoff=(TODAY - timedelta(days=100)).isoformat(),
        years=1,
        api_key="k",
        model="m",
        emit=events.append,
        stream=stream,
        name="ai_test_t2",
    )
    assert result["name"] == "ai_test_t2" and result["attempts"] == 2
    assert result["explanation"] == "- Daha uzun tutuldu."
    assert get_script("ai_test_t2")["source"] == STRATEGY
    assert calls[0][0]["content"].startswith(author.REVISE[:60])
    prompt = calls[0][1]["content"]
    assert "Hisse A" in prompt and "THYAO" not in prompt and not DATE.search(prompt)
    assert "Aşırı satımda sinyali uygula." in prompt and "ta.crossover(f, s)" in prompt
    assert "Derleme hatası" in calls[1][-1]["content"]


def test_revised_names_are_valid_script_names():
    assert blind.revised_name("ai_enkai_tuprs_20261003_0158", 2) == (
        "ai_enkai_tuprs_20261003_0158_t2"
    )
    assert blind.revised_name("ai_x_t2", 3) == "ai_x_t3"
    assert blind.revised_name("editör", 2) == "edit_r_t2"
    assert len(blind.revised_name("a" * 48, 4)) <= 48


def test_author_rejects_scripts_without_entries():
    never = STRATEGY.replace("ta.crossover(f, s)", "close < 0")
    stream, _ = stream_replies(*[f"```pine\n{never}```"] * author.MAX_ATTEMPTS)
    with pytest.raises(ValueError, match="hiçbir hissede giriş"):
        author.author_script(
            ["THYAO"], cutoff=None, years=1, api_key="k", model="m",
            emit=lambda e: None, stream=stream,
        )  # fmt: skip


def test_author_can_be_stopped():
    stream, _ = stream_replies("```pine\n```")
    with pytest.raises(author.Cancelled):
        author.author_script(
            ["THYAO"], cutoff=None, years=1, api_key="k", model="m",
            emit=lambda e: None, cancelled=lambda: True, stream=stream,
        )  # fmt: skip


def test_default_name_is_a_valid_script_name():
    from datetime import datetime

    name = author.default_name(
        ["ENKAI", "TUPRS", "THYAO", "BIMAS", "ASELS"], datetime(2026, 1, 2, 9, 5)
    )
    assert name == "ai_enkai_tuprs_thyao_bimas_20260102_0905"
    assert len(name) <= 48


# --- Jobs and web ------------------------------------------------------------------


def test_job_runner_reports_results_and_errors():
    runner = JobRunner(limit=1)
    done = threading.Event()
    events = []

    def emit(event):
        events.append(event)
        if event["type"] == "done":
            done.set()

    runner.start("x", lambda emit, cancelled: {"ok": 1}, emit)
    assert done.wait(5)
    assert [e["type"] for e in events] == ["result", "done"]
    events.clear()
    done.clear()
    gate = threading.Event()

    def slow(emit, cancelled):
        gate.wait(5)
        raise ValueError("bozuk")

    runner.start("y", slow, emit)
    with pytest.raises(JobError):
        runner.start("z", lambda emit, cancelled: {}, emit)
    gate.set()
    assert done.wait(5)
    assert events[0] == {"type": "error", "message": "bozuk"}


@pytest.fixture
def client():
    app = create_app(TOKEN)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client


def sse_events(response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_web_study_and_signal_blind_test(client):
    body = client.post(
        "/api/lab/study",
        json={"symbols": ["THYAO"], "cutoff": START.isoformat(), "years": 1},
    ).json()
    assert body["symbols"][0]["symbol"] == "THYAO"
    request = {
        "symbols": ["THYAO", "GARAN"],
        "start": START.isoformat(),
        "end": END.isoformat(),
        "source": STRATEGY,
        "mode": "signals",
        "years": 1,
    }
    estimate = client.post("/api/lab/estimate", json=request).json()
    assert estimate["mode"] == "signals" and estimate["decisions"] > 0
    events = sse_events(client.post("/api/lab/blind", json=request))
    assert events[0]["type"] == "start" and events[-1]["type"] == "done"
    (result,) = [e["result"] for e in events if e["type"] == "result"]
    assert result["signals"]["trades"] > 0


def test_web_ai_needs_a_key(client):
    request = {"symbols": ["THYAO"], "start": START.isoformat(), "source": STRATEGY}
    response = client.post("/api/lab/blind", json=request)
    assert response.status_code == 400
    assert "API anahtarı" in response.json()["detail"]
    assert (
        client.post("/api/lab/author", json={"symbols": ["THYAO"]}).status_code == 400
    )
    assert client.post("/api/ai/speed-test", json={}).status_code == 400


def test_web_ai_blind_test(client, monkeypatch, no_models):
    save_settings(
        api_key="sk-or-v1-0123456789abcdef", decision_model="gemini-flash-lite"
    )
    model = RuleModel()
    original = decide.make_decider

    def make(api_key, choice, **kwargs):
        return original(api_key, choice, **kwargs, complete=model)

    monkeypatch.setattr(decide, "make_decider", make)
    request = {
        "symbols": ["THYAO"],
        "start": START.isoformat(),
        "end": END.isoformat(),
        "source": STRATEGY,
        "years": 1,
        "use_cache": False,
    }
    estimate = client.post("/api/lab/estimate", json=request).json()
    assert estimate["model"]["id"] == "google/gemini-3.5-flash-lite"
    events = sse_events(client.post("/api/lab/blind", json=request))
    kinds = [e["type"] for e in events]
    assert "model" in kinds and "decision" in kinds and kinds[-1] == "done"
    (result,) = [e["result"] for e in events if e["type"] == "result"]
    assert result["ai"]["model"]["id"] == "google/gemini-3.5-flash-lite"
    assert result["audit"]["blind"] is True


def test_web_decision_model_settings(client, no_models):
    body = client.get("/api/ai/decision-models").json()
    assert body["current"] == decide.DEFAULT_PRESET
    assert {row["key"] for row in body["presets"]} == set(decide.PRESET_BY_KEY)
    saved = client.post(
        "/api/ai/settings", json={"decision_model": "qwen-flash"}
    ).json()
    assert saved["decision_model"] == "qwen-flash"
    assert client.get("/api/ai/decision-models").json()["current"] == "qwen-flash"


def test_web_catalog(client):
    body = client.get("/api/lab/catalog").json()
    assert len(body["signals"]) == len(research.SIGNALS)
    assert body["max_symbols"] == research.MAX_SYMBOLS


def test_openrouter_complete_chat(monkeypatch):
    sent = []

    class Response:
        status_code = 200

        def json(self):
            return {
                "model": "a/b",
                "choices": [
                    {
                        "message": {"content": [{"type": "text", "text": "hi"}]},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": 0.5},
            }

        def close(self):
            pass

    def send(method, url, **kwargs):
        sent.append(kwargs)
        return Response()

    monkeypatch.setattr(openrouter, "_send", send)
    result = openrouter.complete_chat(
        "k", "a/b", [{"role": "user", "content": "x"}], max_tokens=5,
        extra={"provider": {"sort": "latency"}},
    )  # fmt: skip
    assert result == {
        "text": "hi",
        "model": "a/b",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": 0.5},
    }
    assert sent[0]["json"]["provider"] == {"sort": "latency"}
    assert "stream" not in sent[0]["json"]


def test_frame_dates_are_tz_consistent():
    frame = frame_of()
    assert isinstance(frame.index, pd.DatetimeIndex)
