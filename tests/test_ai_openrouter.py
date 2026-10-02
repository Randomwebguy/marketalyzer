import json
from types import SimpleNamespace

import pytest
import requests

from marketalyzer.ai import openrouter
from marketalyzer.ai.openrouter import (
    FalKey,
    OpenRouterError,
    complete_chat,
    key_info,
    list_models,
    pick_default_model,
    provider_of,
    stream_chat,
)


class FakeResponse:
    def __init__(self, status=200, body=None, lines=(), text=None, broken_at=None):
        self.status_code = status
        self.body = body
        self.text = text if text is not None else json.dumps(body)
        self.lines = list(lines)
        self.broken_at = broken_at
        self.closed = False

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body

    def iter_lines(self):
        for number, line in enumerate(self.lines):
            if number == self.broken_at:
                raise requests.exceptions.ChunkedEncodingError("connection dropped")
            yield line

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fresh_cache():
    openrouter.clear_models_cache()
    yield
    openrouter.clear_models_cache()


@pytest.fixture
def fake_send(monkeypatch):
    fake = SimpleNamespace(responses=[], calls=[])

    def _send(method, url, **kwargs):
        fake.calls.append(SimpleNamespace(method=method, url=url, **kwargs))
        response = fake.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(openrouter, "_send", _send)
    return fake


def sse(*chunks):
    """Encode chunks as SSE lines; strings are sent as they are."""
    lines = []
    for chunk in chunks:
        lines.append(chunk if isinstance(chunk, str) else f"data: {json.dumps(chunk)}")
        lines.append("")
    return lines


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


USAGE = {"usage": {"prompt_tokens": 12, "completion_tokens": 3, "cost": 0.00042}}


def test_stream_text_with_keepalives_and_usage(fake_send):
    response = FakeResponse(
        lines=sse(
            ": OPENROUTER PROCESSING",
            delta(role="assistant", content="Merhaba"),
            delta(reasoning="thinking..."),
            delta(content=", dünya"),
            ": OPENROUTER PROCESSING",
            finish("stop"),
            {"choices": [], **USAGE},
            "data: [DONE]",
            delta(content="after done"),
        )
    )
    fake_send.responses.append(response)
    events = list(
        stream_chat("sk-or-key", "a/b", [{"role": "user", "content": "selam"}])
    )
    assert events == [
        {"type": "text", "text": "Merhaba"},
        {"type": "text", "text": ", dünya"},
        {"type": "finish", "reason": "stop"},
        {"type": "usage", "prompt_tokens": 12, "completion_tokens": 3, "cost": 0.00042},
    ]
    assert response.closed
    (call,) = fake_send.calls
    assert call.method == "POST"
    assert call.url == "https://openrouter.ai/api/v1/chat/completions"
    assert call.stream is True
    assert call.headers["Authorization"] == "Bearer sk-or-key"
    assert (
        call.headers["HTTP-Referer"] == "https://github.com/randomwebguy/marketalyzer"
    )
    assert call.headers["X-Title"] == "marketalyzer"
    assert call.headers["Content-Type"] == "application/json"
    assert call.json == {
        "model": "a/b",
        "messages": [{"role": "user", "content": "selam"}],
        "stream": True,
        "usage": {"include": True},
    }


def test_stream_sends_tools_and_options(fake_send):
    fake_send.responses.append(FakeResponse(lines=sse("data: [DONE]")))
    tools = [{"type": "function", "function": {"name": "quote", "parameters": {}}}]
    assert not list(stream_chat("k", "a/b", [], tools, temperature=0.2, max_tokens=99))
    payload = fake_send.calls[0].json
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 99


def test_stream_decodes_utf8_bytes(fake_send):
    line = f"data: {json.dumps(delta(content='Şişli ığdır'), ensure_ascii=False)}"
    fake_send.responses.append(
        FakeResponse(lines=[line.encode(), b"", b"data: [DONE]"])
    )
    assert list(stream_chat("k", "a/b", [])) == [
        {"type": "text", "text": "Şişli ığdır"}
    ]


def test_stream_fragmented_tool_call(fake_send):
    first = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "quote", "arguments": '{"sym'},
    }
    second = {"index": 0, "function": {"arguments": 'bol": "THYAO"}'}}
    fake_send.responses.append(
        FakeResponse(
            lines=sse(
                delta(tool_calls=[first]),
                delta(tool_calls=[second]),
                finish("tool_calls"),
                "data: [DONE]",
            )
        )
    )
    assert list(stream_chat("k", "a/b", [])) == [
        {
            "type": "tool_call",
            "index": 0,
            "id": "call_1",
            "name": "quote",
            "arguments": '{"sym',
        },
        {
            "type": "tool_call",
            "index": 0,
            "id": None,
            "name": None,
            "arguments": 'bol": "THYAO"}',
        },
        {"type": "finish", "reason": "tool_calls"},
    ]


def test_mid_stream_error(fake_send):
    error = {
        "error": {"code": 502, "message": "Provider disconnected"},
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
    }
    response = FakeResponse(lines=sse(delta(content="Yarım"), error, "data: [DONE]"))
    fake_send.responses.append(response)
    events = stream_chat("k", "a/b", [])
    assert next(events) == {"type": "text", "text": "Yarım"}
    with pytest.raises(OpenRouterError) as raised:
        next(events)
    assert raised.value.status == 502
    assert "geçici bir hata" in str(raised.value)
    assert "(Provider disconnected)" in str(raised.value)
    assert response.closed


def test_mid_stream_error_with_text_code(fake_send):
    error = {"error": {"code": "server_error", "message": "Upstream failed"}}
    fake_send.responses.append(FakeResponse(lines=sse(error)))
    with pytest.raises(OpenRouterError, match="Upstream failed") as raised:
        list(stream_chat("k", "a/b", []))
    assert raised.value.status is None


def test_dropped_stream(fake_send):
    response = FakeResponse(
        lines=sse(delta(content="a"), delta(content="b")), broken_at=2
    )
    fake_send.responses.append(response)
    with pytest.raises(OpenRouterError, match="koptu"):
        list(stream_chat("k", "a/b", []))
    assert response.closed


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (401, "User not found.", "API anahtarı geçersiz"),
        (402, "Insufficient credits", "bakiyesi yetersiz"),
        (403, "Input flagged by moderation", "reddedildi"),
        (404, "Model foo/bar does not exist", "model bulunamadı"),
        (400, "foo/bar is not a valid model ID", "model bulunamadı"),
        (404, "No endpoints found that support tool use.", "araç kullanımını"),
        (408, "Request timed out", "zaman aşımı"),
        (429, "Rate limit exceeded", "istek limitine"),
        (503, "No available providers", "geçici bir hata"),
        (418, "teapot", "HTTP 418"),
    ],
)
def test_http_errors(fake_send, status, message, expected):
    body = {"error": {"code": status, "message": message}}
    response = FakeResponse(status=status, body=body)
    fake_send.responses.append(response)
    with pytest.raises(OpenRouterError) as raised:
        list(stream_chat("k", "a/b", []))
    assert raised.value.status == status
    assert expected in raised.value.message
    assert raised.value.message.endswith(f"({message})")
    assert response.closed


def test_http_error_without_json_body(fake_send):
    fake_send.responses.append(
        FakeResponse(status=502, body=None, text="<html>Bad gateway</html>")
    )
    with pytest.raises(OpenRouterError) as raised:
        list(stream_chat("k", "a/b", []))
    assert "geçici bir hata" in str(raised.value)
    assert "html" not in str(raised.value)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (requests.ConnectionError("refused"), "bağlanılamadı"),
        (requests.exceptions.ConnectTimeout("slow"), "zaman aşımı"),
        (requests.ReadTimeout("slow"), "zaman aşımı"),
    ],
)
def test_network_errors(monkeypatch, error, expected):
    def request(*args, **kwargs):
        raise error

    monkeypatch.setattr(openrouter.requests, "request", request)
    with pytest.raises(OpenRouterError, match=expected) as raised:
        list(stream_chat("k", "a/b", []))
    assert raised.value.status is None
    with pytest.raises(OpenRouterError, match=expected):
        list_models()


MODELS = {
    "data": [
        {
            "id": "z-ai/glm",
            "name": "GLM",
            "context_length": 128000,
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["temperature"],
        },
        {
            "id": "anthropic/claude-sonnet-4.5",
            "name": "Anthropic: Claude Sonnet 4.5",
            "context_length": 1000000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "supported_parameters": ["tools", "tool_choice", "max_tokens"],
        },
        {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
        {"name": "no id"},
    ]
}


def test_list_models(fake_send):
    fake_send.responses.append(FakeResponse(body=MODELS))
    models = list_models()
    assert models == [
        {
            "id": "anthropic/claude-sonnet-4.5",
            "name": "Anthropic: Claude Sonnet 4.5",
            "context_length": 1000000,
            "prompt_price": 3.0,
            "completion_price": 15.0,
            "tools": True,
            "json": False,
        },
        {
            "id": "openrouter/auto",
            "name": "openrouter/auto",
            "context_length": None,
            "prompt_price": None,
            "completion_price": None,
            "tools": False,
            "json": False,
        },
        {
            "id": "z-ai/glm",
            "name": "GLM",
            "context_length": 128000,
            "prompt_price": 0.0,
            "completion_price": 0.0,
            "tools": False,
            "json": False,
        },
    ]
    (call,) = fake_send.calls
    assert (call.method, call.url) == ("GET", "https://openrouter.ai/api/v1/models")
    assert "Authorization" not in call.headers


def test_list_models_is_cached(fake_send, monkeypatch):
    fake_send.responses += [FakeResponse(body=MODELS), FakeResponse(body={"data": []})]
    first = list_models("k")
    first[0]["id"] = "changed by the caller"
    assert list_models("k")[0]["id"] == "anthropic/claude-sonnet-4.5"
    assert len(fake_send.calls) == 1
    assert fake_send.calls[0].headers["Authorization"] == "Bearer k"
    assert list_models("k", refresh=True) == []
    assert len(fake_send.calls) == 2
    # After an hour the list is fetched again.
    fake_send.responses.append(FakeResponse(body=MODELS))
    later = openrouter.time.monotonic() + 3601
    monkeypatch.setattr(openrouter.time, "monotonic", lambda: later)
    assert len(list_models()) == 3
    assert len(fake_send.calls) == 3


def test_list_models_error(fake_send):
    body = {"error": {"code": 401, "message": "No auth credentials found"}}
    fake_send.responses.append(FakeResponse(status=401, body=body))
    with pytest.raises(OpenRouterError, match="API anahtarı geçersiz"):
        list_models("bad")


def test_key_info(fake_send):
    body = {
        "data": {
            "label": "sk-or-v1-abc...xyz",
            "usage": 1.25,
            "limit": None,
            "is_free_tier": False,
            "rate_limit": {"requests": 10},
        }
    }
    fake_send.responses.append(FakeResponse(body=body))
    assert key_info("k") == {
        "provider": "openrouter",
        "label": "sk-or-v1-abc...xyz",
        "usage": 1.25,
        "limit": None,
        "is_free_tier": False,
    }
    call = fake_send.calls[0]
    assert call.url == "https://openrouter.ai/api/v1/key"
    assert call.headers["Authorization"] == "Bearer k"
    with pytest.raises(OpenRouterError, match="ayarlanmamış"):
        key_info("")


def models(*ids, tools=True):
    return [{"id": model_id, "tools": tools} for model_id in ids]


def test_pick_default_model():
    assert (
        pick_default_model(
            models(
                "openai/gpt-5",
                "anthropic/claude-sonnet-4",
                "anthropic/claude-sonnet-5.5",
                "anthropic/claude-sonnet-4.5",
                "anthropic/claude-opus-9",
            )
            + models("anthropic/claude-sonnet-6", tools=False)
        )
        == "anthropic/claude-sonnet-5.5"
    )
    assert (
        pick_default_model(
            models("anthropic/claude-sonnet-4.5:beta", "anthropic/claude-sonnet-4.5")
        )
        == "anthropic/claude-sonnet-4.5"
    )
    assert (
        pick_default_model(
            models(
                "openai/gpt-5",
                "anthropic/claude-3.5-haiku",
                "anthropic/claude-3.7-sonnet",
            )
        )
        == "anthropic/claude-3.7-sonnet"
    )
    assert (
        pick_default_model(
            models("anthropic/claude-sonnet-4.5", tools=False)
            + models("openai/gpt-5", "google/gemini-3-pro")
        )
        == "openai/gpt-5"
    )
    assert pick_default_model(models("a/b", tools=False)) is None
    assert pick_default_model([]) is None


FAL = FalKey("fal_sk_0123:abcdef")
FAL_CHAT = "https://fal.run/openrouter/router/openai/v1/chat/completions"


def test_fal_key_streams_through_fal(fake_send):
    fake_send.responses.append(
        FakeResponse(lines=sse(delta(content="Merhaba"), finish("stop"), USAGE))
    )
    events = list(stream_chat(FAL, "a/b", [{"role": "user", "content": "selam"}]))
    assert events[0] == {"type": "text", "text": "Merhaba"}
    assert events[-1]["cost"] == 0.00042
    (call,) = fake_send.calls
    assert call.url == FAL_CHAT
    assert call.headers["Authorization"] == "Key fal_sk_0123:abcdef"
    assert call.json["model"] == "a/b"


def test_fal_key_completes_through_fal(fake_send):
    body = {
        "model": "a/b",
        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 1, "cost": 1.1e-06},
    }
    fake_send.responses.append(FakeResponse(body=body))
    result = complete_chat(FAL, "a/b", [{"role": "user", "content": "selam"}])
    assert result["text"] == "OK"
    assert result["usage"]["cost"] == 1.1e-06
    (call,) = fake_send.calls
    assert call.url == FAL_CHAT
    assert call.headers["Authorization"] == "Key fal_sk_0123:abcdef"


def test_fal_models_come_from_openrouters_public_list(fake_send):
    # fal.ai lists no models; it serves OpenRouter's model ids.
    fake_send.responses.append(FakeResponse(body=MODELS))
    assert len(list_models(FAL)) == 3
    (call,) = fake_send.calls
    assert call.url == "https://openrouter.ai/api/v1/models"
    assert "Authorization" not in call.headers


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"detail": "invalid key credentials"}, "fal.ai API anahtarı geçersiz"),
        (402, {"detail": "insufficient balance"}, "fal.ai bakiyesi yetersiz"),
        (500, {"detail": "boom"}, "fal.ai veya model sağlayıcısı"),
    ],
)
def test_fal_errors_name_fal(fake_send, status, body, expected):
    fake_send.responses.append(FakeResponse(status=status, body=body))
    with pytest.raises(OpenRouterError, match=expected) as raised:
        list(stream_chat(FAL, "a/b", []))
    assert raised.value.status == status
    assert body["detail"] in raised.value.message
    assert "OpenRouter" not in raised.value.message


def test_fal_network_errors_name_fal(monkeypatch):
    def request(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(openrouter.requests, "request", request)
    with pytest.raises(OpenRouterError, match="fal.ai'a bağlanılamadı"):
        list(stream_chat(FAL, "a/b", []))


def test_fal_key_info_runs_a_one_token_completion(fake_send):
    body = {
        "model": "google/gemini-9-flash-lite",
        "choices": [{"message": {"content": "O"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 1, "cost": 1.1e-06},
    }
    fake_send.responses.append(FakeResponse(body=body))
    assert key_info(FAL, model="google/gemini-9-flash-lite") == {
        "provider": "fal",
        "label": "fal.ai",
        "model": "google/gemini-9-flash-lite",
        "cost": 1.1e-06,
    }
    (call,) = fake_send.calls
    assert call.url == FAL_CHAT
    assert call.json["max_tokens"] == 1
    assert call.json["model"] == "google/gemini-9-flash-lite"


def test_provider_of_a_key():
    assert provider_of(FAL) == "fal"
    assert provider_of("sk-or-v1-abc") == "openrouter"
    assert provider_of(None) == "openrouter"
