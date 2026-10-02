import json

import pytest
from fastapi.testclient import TestClient

from marketalyzer.ai import openrouter
from marketalyzer.ai.agent import ToolError
from marketalyzer.ai.chat import trim_history
from marketalyzer.ai.settings import save_settings
from marketalyzer.ai.tools import Tools
from marketalyzer.paper.account import PaperAccount
from marketalyzer.paper.cli import account_path
from marketalyzer.web.app import create_app

TOKEN = "test-token"
SCRIPT = """//@version=5
strategy("Deneme", overlay=true)
length = input.int(10, "Uzunluk", minval=5, maxval=20, step=5)
average = ta.sma(close, length)
plot(average, "SMA")
if ta.crossover(close, average)
    strategy.entry("Al", strategy.long)
if ta.crossunder(close, average)
    strategy.close("Al")
"""


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MARKETALYZER_AI_MODEL", raising=False)


@pytest.fixture
def thyao(fake_fetch, prices, as_rows):
    for symbol in ("THYAO", "GARAN", "XU100"):
        fake_fetch.responses[("equity", symbol)] = as_rows(prices)
        fake_fetch.responses[("index", symbol)] = as_rows(prices)
    return fake_fetch


@pytest.fixture
def client():
    app = create_app(TOKEN)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        client.app_state = app.state
        yield client


def sse_events(response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def scripted_stream(*steps):
    """A fake model: each call yields the next list of stream events."""
    calls = []

    def stream(api_key, model, messages, tools):
        calls.append({"model": model, "messages": messages, "tools": tools})
        yield from steps[len(calls) - 1]

    stream.calls = calls
    return stream


class TestTools:
    def test_specs_are_valid_function_schemas(self, tmp_path):
        specs = Tools(tmp_path / "paper.sqlite").specs()
        names = [spec["function"]["name"] for spec in specs]
        assert len(names) == len(set(names)) >= 15
        for spec in specs:
            assert spec["type"] == "function"
            assert spec["function"]["parameters"]["type"] == "object"
            assert spec["function"]["description"]

    def test_analysis_backtest_and_scripts(self, tmp_path, thyao):
        events = []
        tools = Tools(tmp_path / "paper.sqlite", on_event=events.append)
        snapshot = tools.call("technical_analysis", {"symbol": "thyao"})
        assert snapshot["symbol"] == "THYAO" and 0 <= snapshot["rsi14"] <= 100
        assert snapshot["signals"]

        checked = tools.call("check_script", {"source": "x = ta.sma(close)"})
        assert checked["ok"] is False and checked["error"]["line"] == 1

        result = tools.call(
            "backtest",
            {"symbol": "THYAO", "source": SCRIPT, "start": "2024-01-02"},
        )
        assert result["strategy"] == "editör scripti"
        assert result["params"] == {"length": 10}
        assert len(result["trades_shown"]) <= 12

        saved = tools.call("save_script", {"name": "deneme", "source": SCRIPT})
        assert saved["strategy_name"] == "script:deneme"
        assert events[-1] == {"type": "script_saved", "name": "deneme"}

        run = tools.call("run_script", {"symbol": "THYAO", "name": "deneme"})
        assert set(run["plots"]) == {"SMA"} and len(run["plots"]["SMA"]) == 5

        screen = tools.call(
            "screen_symbols", {"symbols": ["THYAO", "YOK"], "name": "script:deneme"}
        )
        rows = {row["symbol"]: row for row in screen["results"]}
        assert "in_position" in rows["THYAO"] and "error" in rows["YOK"]

    def test_tool_errors_are_reported_to_the_model(self, tmp_path, thyao):
        tools = Tools(tmp_path / "paper.sqlite")
        with pytest.raises(ToolError, match="Bilinmeyen strateji"):
            tools.call("backtest", {"symbol": "THYAO", "strategy": "yok"})
        with pytest.raises(ToolError, match="Geçersiz argümanlar"):
            tools.call("get_quotes", {"symbolz": ["THYAO"]})

    def test_paper_orders_need_permission(self, tmp_path):
        path = account_path("web")
        PaperAccount.create(path, 10_000).close()
        tools = Tools(path)
        order = {"symbol": "THYAO", "side": "buy", "qty": 5}
        with pytest.raises(ToolError, match="izin"):
            tools.call("paper_order", order)
        save_settings(allow_trading=True)
        placed = tools.call("paper_order", order)
        assert (placed["status"], placed["tag"]) == ("open", "ai")
        assert tools.call("paper_account", {})["open_orders"][0]["id"] == placed["id"]
        assert tools.call("paper_cancel", {"order_id": placed["id"]})["status"] == (
            "cancelled"
        )


def test_trim_history_keeps_whole_exchanges():
    messages = []
    for n in range(5):
        messages += [
            {"role": "user", "content": f"soru {n} " + "x" * 100},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{n}"}]},
            {"role": "tool", "tool_call_id": f"c{n}", "content": "y" * 100},
            {"role": "assistant", "content": "cevap"},
        ]
    trimmed = trim_history(messages, limit=700)
    assert trimmed[0]["role"] == "user"
    assert 0 < len(trimmed) < len(messages)
    assert trim_history(messages[-4:], limit=1) == messages[-4:]


class TestAIEndpoints:
    def test_settings_never_return_the_key(self, client):
        assert client.get("/api/ai/settings").json()["configured"] is False
        saved = client.post(
            "/api/ai/settings",
            json={"api_key": "sk-or-v1-abcdef1234567890", "model": "a/b"},
        ).json()
        assert saved["configured"] is True
        assert saved["key_hint"].endswith("7890")
        assert "abcdef" not in json.dumps(saved)
        assert client.get("/api/meta").json()["ai"]["model"] == "a/b"
        assert (
            client.post("/api/ai/settings", json={"api_key": "bad key"}).status_code
            == 400
        )
        cleared = client.post("/api/ai/settings", json={"clear_key": True}).json()
        assert cleared["configured"] is False

    def test_models_and_key_test(self, client, monkeypatch):
        models = [{"id": "anthropic/claude-sonnet-4.5", "tools": True}]
        monkeypatch.setattr("marketalyzer.web.app.list_models", lambda *a, **k: models)
        body = client.get("/api/ai/models").json()
        assert body["default"] == "anthropic/claude-sonnet-4.5"
        assert client.post("/api/ai/test").status_code == 400

        def broken(api_key):
            raise openrouter.OpenRouterError("Anahtar geçersiz", 401)

        monkeypatch.setattr("marketalyzer.web.app.key_info", broken)
        save_settings(api_key="sk-or-v1-abcdef1234567890")
        response = client.post("/api/ai/test")
        assert response.status_code == 502 and "geçersiz" in response.json()["detail"]

    def test_chat_needs_a_key(self, client):
        response = client.post("/api/ai/chat", json={"message": "merhaba"})
        assert response.status_code == 400
        assert "API anahtarı" in response.json()["detail"]

    def test_chat_streams_tool_calls_and_saves_the_conversation(self, client, thyao):
        save_settings(api_key="sk-or-v1-abcdef1234567890", model="test/model")
        stream = scripted_stream(
            [
                {
                    "type": "tool_call",
                    "index": 0,
                    "id": "c1",
                    "name": "get_quotes",
                    "arguments": '{"symbols": ',
                },
                {"type": "tool_call", "index": 0, "arguments": '["THYAO"]}'},
                {"type": "finish", "reason": "tool_calls"},
                {
                    "type": "usage",
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cost": 0.001,
                },
            ],
            [
                {"type": "text", "text": "THYAO son fiyat "},
                {"type": "text", "text": "verildi."},
                {"type": "finish", "reason": "stop"},
                {
                    "type": "usage",
                    "prompt_tokens": 150,
                    "completion_tokens": 20,
                    "cost": 0.002,
                },
            ],
        )
        client.app_state.chat.stream = stream
        response = client.post(
            "/api/ai/chat",
            json={"message": "THYAO ne durumda?", "context": {"symbol": "THYAO"}},
        )
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events(response)
        kinds = [event["type"] for event in events]
        assert kinds[0] == "start" and kinds[-1] == "done"
        assert kinds.index("tool_start") < kinds.index("tool_end") < kinds.index("text")
        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert tool_end["ok"] and tool_end["result"][0]["symbol"] == "THYAO"
        system = stream.calls[0]["messages"][0]["content"]
        assert "Seçili sembol: THYAO" in system and "ta.sma(source, length)" in system

        conversation_id = events[-1]["conversation_id"]
        listed = client.get("/api/ai/conversations").json()
        assert listed[0]["id"] == conversation_id
        assert listed[0]["title"] == "THYAO ne durumda?"
        stored = client.get(f"/api/ai/conversations/{conversation_id}").json()
        roles = [m["role"] for m in stored["messages"]]
        assert roles == ["user", "tool", "assistant"]
        assert stored["messages"][-1]["text"] == "THYAO son fiyat verildi."
        assert stored["usage"]["prompt_tokens"] == 250

        # The next turn sends the stored history back to the model.
        follow_up = scripted_stream([{"type": "text", "text": "Tamam."}])
        client.app_state.chat.stream = follow_up
        client.post(
            "/api/ai/chat",
            json={"message": "teşekkürler", "conversation_id": conversation_id},
        )
        sent = follow_up.calls[0]["messages"]
        assert [m["role"] for m in sent[1:]] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "user",
        ]
        assert (
            client.delete(f"/api/ai/conversations/{conversation_id}").status_code == 200
        )
        assert client.get(f"/api/ai/conversations/{conversation_id}").status_code == 404

    def test_provider_errors_end_the_stream(self, client):
        save_settings(api_key="sk-or-v1-abcdef1234567890", model="test/model")

        def failing(api_key, model, messages, tools):
            raise openrouter.OpenRouterError("Bakiye yetersiz", 402)
            yield  # pragma: no cover

        client.app_state.chat.stream = failing
        events = sse_events(client.post("/api/ai/chat", json={"message": "selam"}))
        assert [e["type"] for e in events] == ["start", "model", "error", "done"]
        assert events[2]["message"] == "Bakiye yetersiz"
        assert client.post("/api/ai/stop/" + "0" * 32).json() == {"stopped": False}


class TestScriptEndpoints:
    def test_crud_check_and_run(self, client, thyao):
        listed = {s["name"]: s for s in client.get("/api/scripts").json()}
        assert listed["rsi"]["kind"] == "indicator"
        assert (
            client.get("/api/scripts/rsi").json()["source"].startswith("//@version=5")
        )
        assert client.get("/api/scripts/yok").status_code == 404

        saved = client.put("/api/scripts/benim", json={"source": SCRIPT}).json()
        assert saved["kind"] == "strategy" and saved["builtin"] is False
        assert "script:benim" in client.get("/api/strategies").json()
        assert (
            client.put("/api/scripts/Kötü Ad", json={"source": SCRIPT}).status_code
            == 400
        )

        bad = client.post(
            "/api/scripts/check", json={"source": "plot(ta.rsi(close))"}
        ).json()
        assert bad["ok"] is False and "length" in bad["error"]["message"]

        run = client.post(
            "/api/scripts/run", json={"symbol": "THYAO", "name": "benim", "span": "1Y"}
        ).json()
        assert run["plots"][0]["title"] == "SMA"
        assert len(run["plots"][0]["values"]) == len(run["rows"])
        assert run["entries"] and run["script"]["kind"] == "strategy"

        failing = client.post(
            "/api/scripts/run", json={"symbol": "THYAO", "source": "plot(clse)"}
        )
        assert failing.status_code == 400
        assert failing.json()["detail"]["line"] == 1

        backtest = client.post(
            "/api/backtest",
            json={
                "symbol": "THYAO",
                "strategy": "script:benim",
                "benchmark": False,
                "start": "2024-01-02",
            },
        ).json()
        assert backtest["strategy"] == "script:benim" and backtest["trade_list"]

        assert client.delete("/api/scripts/benim").json()["restored_builtin"] is False
        assert client.delete("/api/scripts/rsi").status_code == 400

    def test_reference_and_analysis(self, client, thyao):
        entries = client.get("/api/scripts/reference").json()
        assert any(e["name"] == "ta.supertrend" for e in entries)
        analysis = client.get("/api/analysis?symbol=THYAO").json()
        assert analysis["moving_averages"]["sma50"] is not None
