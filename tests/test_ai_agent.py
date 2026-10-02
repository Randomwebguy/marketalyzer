import json

import pytest

from marketalyzer.ai.agent import (
    MAX_RESULT_CHARS,
    TRUNCATED,
    ToolError,
    run_turn,
    tool_outcome,
)
from marketalyzer.ai.openrouter import OpenRouterError

HISTORY = [{"role": "user", "content": "THYAO kaç TL?"}]


class Tools:
    """A registry with a quote tool and tools that fail."""

    def __init__(self, on_call=None):
        self.calls = []
        self.on_call = on_call

    def specs(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in ("quote", "refuse", "crash", "huge", "nan")
        ]

    def call(self, name, args):
        self.calls.append((name, args))
        if self.on_call:
            self.on_call(name, args)
        if name == "refuse":
            raise ToolError("Bu sembol bulunamadı.")
        if name == "crash":
            raise RuntimeError("boom")
        if name == "huge":
            return "x" * (MAX_RESULT_CHARS * 2)
        if name == "nan":
            return {"sharpe": float("nan"), "when": Opaque()}
        return {"symbol": args.get("symbol"), "last": 300.5}


class Opaque:
    def __str__(self):
        return "opaque"


class Script:
    """A fake ``stream_chat`` that replays one event list per model call."""

    def __init__(self, *steps, repeat=False):
        self.steps = list(steps)
        self.repeat = repeat
        self.requests = []
        self.closed = 0

    def __call__(self, api_key, model, messages, tools=None):
        self.requests.append(
            {"api_key": api_key, "model": model, "messages": messages, "tools": tools}
        )
        step = self.steps[0] if self.repeat else self.steps.pop(0)
        return self._events(step)

    def _events(self, step):
        try:
            for event in step:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            self.closed += 1


def text(value):
    return {"type": "text", "text": value}


def call(index, name=None, arguments=None, id=None):
    return {
        "type": "tool_call",
        "index": index,
        "id": id,
        "name": name,
        "arguments": arguments,
    }


def finish(reason):
    return {"type": "finish", "reason": reason}


def usage(prompt, completion, cost):
    return {
        "type": "usage",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost": cost,
    }


def run(script, tools=None, **kwargs):
    events = []
    result = run_turn(
        HISTORY,
        api_key="sk-or-test",
        model="a/b",
        tools=tools or Tools(),
        system_prompt="Sen bir asistansın.",
        emit=events.append,
        stream=script,
        **kwargs,
    )
    return result, events


def tool_step(name="quote", arguments='{"symbol": "THYAO"}', id="call_a"):
    return [call(0, name, arguments, id), finish("tool_calls")]


def test_tool_call_then_answer():
    script = Script(
        [
            text("Bakıyorum."),
            call(0, "quote", '{"sym', "call_a"),
            call(0, None, 'bol": "THYAO"}'),
            finish("tool_calls"),
            usage(100, 20, 0.001),
        ],
        [text("THYAO "), text("300,5 TL."), finish("stop"), usage(150, 10, 0.002)],
    )
    tools = Tools()
    result, events = run(script, tools)

    assert tools.calls == [("quote", {"symbol": "THYAO"})]
    assert events == [
        {"type": "text", "text": "Bakıyorum."},
        {
            "type": "tool_start",
            "id": "call_a",
            "name": "quote",
            "args": {"symbol": "THYAO"},
        },
        {
            "type": "tool_end",
            "id": "call_a",
            "name": "quote",
            "ok": True,
            "result": {"symbol": "THYAO", "last": 300.5},
        },
        {"type": "text", "text": "THYAO "},
        {"type": "text", "text": "300,5 TL."},
        {
            "type": "usage",
            "prompt_tokens": 250,
            "completion_tokens": 30,
            "cost": pytest.approx(0.003),
        },
    ]
    assert result.messages == [
        {
            "role": "assistant",
            "content": "Bakıyorum.",
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "quote", "arguments": '{"symbol": "THYAO"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_a",
            "content": '{"symbol": "THYAO", "last": 300.5}',
        },
        {"role": "assistant", "content": "THYAO 300,5 TL."},
    ]
    assert result.usage == {
        "prompt_tokens": 250,
        "completion_tokens": 30,
        "cost": pytest.approx(0.003),
    }
    assert result.stopped is False

    first, second = script.requests
    assert first["api_key"] == "sk-or-test"
    assert first["model"] == "a/b"
    assert first["messages"] == [
        {"role": "system", "content": "Sen bir asistansın."},
        *HISTORY,
    ]
    assert [spec["function"]["name"] for spec in first["tools"]][0] == "quote"
    assert second["messages"] == [
        {"role": "system", "content": "Sen bir asistansın."},
        *HISTORY,
        *result.messages[:2],
    ]
    assert script.closed == 2


def test_parallel_calls_and_missing_ids():
    script = Script(
        [
            call(0, "quote", '{"symbol": "THYAO"}'),
            call(1, "quote", ""),
            call(1, None, '{"symbol": "ASELS"}'),
            finish("tool_calls"),
        ],
        [text("Tamam."), finish("stop")],
    )
    tools = Tools()
    result, events = run(script, tools)
    assistant, first, second, answer = result.messages
    assert assistant["content"] is None
    ids = [tool_call["id"] for tool_call in assistant["tool_calls"]]
    assert all(id.startswith("call_") for id in ids)
    assert len(set(ids)) == 2
    assert [first["tool_call_id"], second["tool_call_id"]] == ids
    assert tools.calls == [
        ("quote", {"symbol": "THYAO"}),
        ("quote", {"symbol": "ASELS"}),
    ]
    assert [e["id"] for e in events if e["type"] == "tool_start"] == ids
    assert answer == {"role": "assistant", "content": "Tamam."}


def test_tool_without_arguments():
    tools = Tools()
    result, _ = run(Script(tool_step(arguments=None), [text("ok")]), tools)
    assert tools.calls == [("quote", {})]
    assert result.messages[0]["tool_calls"][0]["function"]["arguments"] == "{}"


@pytest.mark.parametrize("arguments", ['{"symbol": "THY', '["THYAO"]'])
def test_invalid_arguments_are_reported_to_the_model(arguments):
    tools = Tools()
    result, events = run(Script(tool_step(arguments=arguments), [text("ok")]), tools)
    assert tools.calls == []
    start, end = (e for e in events if e["type"].startswith("tool_"))
    assert start["args"] == {}
    assert end["ok"] is False
    assert "Geçersiz araç argümanları" in end["result"]
    assert f"Gönderilen: {arguments}" in end["result"]
    assert json.loads(result.messages[1]["content"]) == {"error": end["result"]}
    # The history sent back to the model only holds valid JSON arguments.
    assert result.messages[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert result.messages[-1] == {"role": "assistant", "content": "ok"}


def test_tool_error():
    result, events = run(Script(tool_step("refuse"), [text("Bulunamadı.")]))
    end = events[1]
    assert end == {
        "type": "tool_end",
        "id": "call_a",
        "name": "refuse",
        "ok": False,
        "result": "Bu sembol bulunamadı.",
    }
    assert json.loads(result.messages[1]["content"]) == {
        "error": "Bu sembol bulunamadı."
    }


def test_unexpected_exception_does_not_end_the_turn():
    result, events = run(Script(tool_step("crash"), [text("Üzgünüm.")]))
    assert events[1]["ok"] is False
    assert events[1]["result"] == "Araç hatası: RuntimeError: boom"
    assert result.messages[-1]["content"] == "Üzgünüm."


def test_unknown_tool():
    tools = Tools()
    _, events = run(Script(tool_step("delete_everything"), [text("ok")]), tools)
    assert tools.calls == []
    assert events[1]["result"] == "Bilinmeyen araç: delete_everything"


def test_long_results_are_truncated():
    result, events = run(Script(tool_step("huge"), [text("ok")]))
    content = result.messages[1]["content"]
    assert len(content) == MAX_RESULT_CHARS + len(TRUNCATED)
    assert content.endswith("…(kısaltıldı)")
    assert events[1]["ok"] is True
    assert events[1]["result"] == content


def test_results_are_valid_json():
    result, events = run(Script(tool_step("nan"), [text("ok")]))
    assert result.messages[1]["content"] == '{"sharpe": null, "when": "opaque"}'
    assert events[1]["result"] == {"sharpe": None, "when": "opaque"}


def test_max_steps():
    script = Script(tool_step(), repeat=True)
    tools = Tools()
    result, events = run(script, tools, max_steps=3)
    assert len(script.requests) == 3
    assert len(tools.calls) == 3
    assert [m["role"] for m in result.messages] == ["assistant", "tool"] * 3
    notices = [e for e in events if e["type"] == "notice"]
    assert len(notices) == 1
    assert "Adım sınırına" in notices[0]["text"]
    assert events[-1]["type"] == "usage"
    assert result.stopped is False


def test_cancel_while_streaming():
    flag = {"cancelled": False}

    def chunks():
        yield text("Kısmi ")
        yield text("yanıt")
        flag["cancelled"] = True
        yield call(0, "quote", '{"symbol": "THYAO"}', "call_a")
        yield finish("tool_calls")
        pytest.fail("the stream should have been closed")

    script = Script(chunks())
    tools = Tools()
    result, events = run(script, tools, cancelled=lambda: flag["cancelled"])
    assert result.stopped is True
    assert result.messages == [{"role": "assistant", "content": "Kısmi yanıt"}]
    assert tools.calls == []
    assert script.closed == 1
    assert [e["type"] for e in events] == ["text", "text", "usage"]


def test_cancel_between_tool_calls():
    flag = {"cancelled": False}

    def cancel(name, args):
        flag["cancelled"] = True

    script = Script(
        [
            call(0, "quote", '{"symbol": "THYAO"}', "call_a"),
            call(1, "quote", '{"symbol": "ASELS"}', "call_b"),
            finish("tool_calls"),
        ],
        [text("never")],
    )
    tools = Tools(on_call=cancel)
    result, events = run(script, tools, cancelled=lambda: flag["cancelled"])
    assert result.stopped is True
    assert len(script.requests) == 1
    assert tools.calls == [("quote", {"symbol": "THYAO"})]
    # Every tool call still gets a result, so the history stays valid.
    assistant, done, skipped = result.messages
    assert [c["id"] for c in assistant["tool_calls"]] == ["call_a", "call_b"]
    assert done["tool_call_id"] == "call_a"
    assert skipped["tool_call_id"] == "call_b"
    assert tool_outcome(skipped["content"])[0] is False
    assert [e["type"] for e in events] == ["tool_start", "tool_end", "usage"]


def test_cancelled_before_start():
    script = Script([text("never")])
    result, events = run(script, cancelled=lambda: True)
    assert result.stopped is True
    assert result.messages == []
    assert script.requests == []
    assert events == [
        {"type": "usage", "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    ]


def test_model_error_keeps_completed_messages():
    failure = OpenRouterError("OpenRouter bakiyesi yetersiz.", 402)
    script = Script(tool_step(), [text("Yarım "), failure])
    with pytest.raises(OpenRouterError) as raised:
        run(script)
    partial = raised.value.partial
    assert [m["role"] for m in partial.messages] == ["assistant", "tool", "assistant"]
    assert partial.messages[-1] == {"role": "assistant", "content": "Yarım "}
    assert raised.value.status == 402


def test_empty_and_truncated_replies_are_noticed():
    _, events = run(Script([finish("stop")]))
    assert [e["type"] for e in events] == ["notice", "usage"]
    assert "boş bir yanıt" in events[0]["text"]
    result, events = run(Script([text("Uzun"), finish("length")]))
    assert result.messages == [{"role": "assistant", "content": "Uzun"}]
    assert "çıktı sınırına" in events[1]["text"]


def test_registry_without_tools():
    class NoTools:
        def specs(self):
            return []

        def call(self, name, args):
            raise AssertionError

    script = Script([text("Merhaba")])
    result, _ = run(script, NoTools())
    assert script.requests[0]["tools"] is None
    assert result.messages == [{"role": "assistant", "content": "Merhaba"}]
