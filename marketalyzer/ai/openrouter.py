"""A small client for OpenRouter's OpenAI-compatible API.

Every HTTP request goes through ``_send``, so tests replace that one function.
Errors become ``OpenRouterError`` with a Turkish message for the UI.
"""

import json
import re
import threading
import time
from collections.abc import Iterable, Iterator
from typing import Any

import requests

BASE_URL = "https://openrouter.ai/api/v1"
REFERER = "https://github.com/randomwebguy/marketalyzer"
TITLE = "marketalyzer"
MODELS_TTL = 3600.0
# (connect, read) timeouts in seconds. While streaming, the read timeout is the
# longest silence allowed between chunks; OpenRouter sends keepalive comments.
TIMEOUT = (10.0, 30.0)
STREAM_TIMEOUT = (10.0, 180.0)
MAX_DETAIL = 300

_models_lock = threading.Lock()
_models_cache: tuple[float, list[dict[str, Any]]] | None = None


class OpenRouterError(Exception):
    """A failed OpenRouter request, with a Turkish message for the UI.

    Attributes
    ----------
    status
        The HTTP status (or the error code OpenRouter reported), or None for
        network errors.
    message
        The user-facing message, also ``str(error)``.
    partial
        Set by ``run_turn``: the messages completed before the failure.
    """

    partial: Any = None

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


# Messages for HTTP statuses that need no further detail.
STATUS_MESSAGES = {
    401: "OpenRouter API anahtarı geçersiz veya iptal edilmiş. Ayarlar'dan"
    " anahtarı kontrol edin.",
    402: "OpenRouter bakiyesi yetersiz. openrouter.ai üzerinden kredi yükleyin"
    " veya ücretsiz bir model seçin.",
    403: "İstek OpenRouter tarafından reddedildi (içerik denetimi veya erişim izni).",
    408: "OpenRouter isteği zaman aşımına uğradı. Biraz sonra tekrar deneyin.",
    429: "OpenRouter istek limitine ulaşıldı. Biraz sonra tekrar deneyin.",
}
NO_TOOLS = (
    "Seçilen model araç kullanımını (tool calling) desteklemiyor. Ayarlar'dan"
    " başka bir model seçin."
)
NO_MODEL = (
    "Seçilen model bulunamadı veya desteklenmiyor. Ayarlar'dan başka bir model seçin."
)
PROVIDER_DOWN = (
    "OpenRouter veya model sağlayıcısı geçici bir hata verdi. Biraz sonra tekrar"
    " deneyin ya da başka bir model seçin."
)


def _base_message(status: int | None, detail: str) -> str:
    text = detail.lower()
    if status == 404 and "tool" in text:
        return NO_TOOLS  # "No endpoints found that support tool use."
    if status in (400, 404) and ("model" in text or "endpoint" in text):
        return NO_MODEL
    if status is not None and status >= 500:
        return PROVIDER_DOWN
    if status is None:
        return "OpenRouter bir hata bildirdi."
    default = f"OpenRouter isteği başarısız oldu (HTTP {status})."
    return STATUS_MESSAGES.get(status, default)


def api_error(status: int | None, detail: str | None = None) -> OpenRouterError:
    """Build the error for an HTTP status and the provider's own error text."""
    detail = " ".join((detail or "").split())[:MAX_DETAIL]
    message = _base_message(status, detail)
    if detail:
        message = f"{message} ({detail})"
    return OpenRouterError(message, status)


def _network_error(error: requests.RequestException) -> OpenRouterError:
    if isinstance(error, requests.Timeout):
        return OpenRouterError(
            "OpenRouter yanıt vermedi (zaman aşımı). Biraz sonra tekrar deneyin."
        )
    if isinstance(error, requests.exceptions.ChunkedEncodingError):
        return OpenRouterError(
            "OpenRouter bağlantısı yanıt sırasında koptu. Tekrar deneyin."
        )
    if isinstance(error, requests.ConnectionError):
        return OpenRouterError(
            "OpenRouter'a bağlanılamadı. İnternet bağlantınızı veya ağ erişimini"
            " (güvenlik duvarı, proxy) kontrol edin."
        )
    return OpenRouterError(f"OpenRouter isteği başarısız oldu: {type(error).__name__}.")


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {
        "HTTP-Referer": REFERER,
        "X-Title": TITLE,
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _send(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any] | None = None,
    stream: bool = False,
    timeout: float | tuple[float, float] = TIMEOUT,
) -> requests.Response:
    """Make one HTTP request: the only place that touches the network."""
    try:
        return requests.request(
            method, url, headers=headers, json=json, stream=stream, timeout=timeout
        )
    except requests.RequestException as error:
        raise _network_error(error) from error


def _error_detail(body: Any) -> tuple[Any, str | None]:
    """Return the code and message of an OpenRouter error body."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        return error.get("code"), str(message) if message else None
    if isinstance(error, str):
        return None, error
    return None, None


def _check(response: requests.Response) -> None:
    """Raise OpenRouterError for a non-2xx response."""
    if 200 <= response.status_code < 300:
        return
    try:
        body = response.json()
    except ValueError:
        text = (getattr(response, "text", "") or "").strip()
        body = {"error": text} if text and not text.startswith("<") else None
    finally:
        response.close()
    raise api_error(response.status_code, _error_detail(body)[1])


def _get(path: str, api_key: str | None) -> dict[str, Any]:
    response = _send("GET", f"{BASE_URL}{path}", headers=_headers(api_key))
    _check(response)
    try:
        body = response.json()
    except ValueError:
        raise OpenRouterError("OpenRouter beklenmeyen bir yanıt döndürdü.") from None
    finally:
        response.close()
    if not isinstance(body, dict):
        raise OpenRouterError("OpenRouter beklenmeyen bir yanıt döndürdü.")
    return body


def _per_million(price: Any) -> float | None:
    """Convert a per-token USD price string to USD per million tokens."""
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    return round(value * 1_000_000, 6) if value >= 0 else None


def _model(item: dict[str, Any]) -> dict[str, Any]:
    pricing = item.get("pricing") or {}
    context = item.get("context_length")
    supported = item.get("supported_parameters") or []
    return {
        "id": item["id"],
        "name": item.get("name") or item["id"],
        "context_length": context if isinstance(context, int) else None,
        "prompt_price": _per_million(pricing.get("prompt")),
        "completion_price": _per_million(pricing.get("completion")),
        "tools": "tools" in supported,
        "json": "response_format" in supported or "structured_outputs" in supported,
    }


def list_models(
    api_key: str | None = None, *, refresh: bool = False
) -> list[dict[str, Any]]:
    """Return OpenRouter's models, sorted by id; cached for an hour.

    Each model is ``{"id", "name", "context_length", "prompt_price",
    "completion_price", "tools", "json"}``: prices are USD per million tokens
    (None when unknown), ``tools`` tells whether the model supports tool calling
    and ``json`` whether it can be asked for a JSON object.
    """
    global _models_cache  # noqa: PLW0603
    with _models_lock:
        cached = _models_cache
    if not refresh and cached and time.monotonic() - cached[0] < MODELS_TTL:
        return [dict(model) for model in cached[1]]
    data = _get("/models", api_key).get("data") or []
    models = sorted(
        (_model(item) for item in data if isinstance(item, dict) and item.get("id")),
        key=lambda model: model["id"],
    )
    with _models_lock:
        _models_cache = (time.monotonic(), models)
    return [dict(model) for model in models]


def clear_models_cache() -> None:
    """Forget the cached model list."""
    global _models_cache  # noqa: PLW0603
    with _models_lock:
        _models_cache = None


def key_info(api_key: str) -> dict[str, Any]:
    """Check a key: return its ``label``, ``usage``, ``limit`` and ``is_free_tier``."""
    if not api_key:
        raise OpenRouterError("OpenRouter API anahtarı ayarlanmamış.")
    data = _get("/key", api_key).get("data") or {}
    return {
        "label": data.get("label"),
        "usage": data.get("usage"),
        "limit": data.get("limit"),
        "is_free_tier": data.get("is_free_tier"),
    }


def _version(model_id: str) -> tuple[tuple[int, ...], bool]:
    """Sort key: the numbers in the id, then plain ids before ":variant" ones."""
    numbers = tuple(int(part) for part in re.findall(r"\d+", model_id))
    return numbers, ":" not in model_id


def pick_default_model(models: Iterable[dict[str, Any]]) -> str | None:
    """Pick a tool-capable model: the newest Claude Sonnet, else the newest Claude."""
    capable = [model["id"] for model in models if model.get("tools")]
    for prefix in ("anthropic/claude-sonnet", "anthropic/claude"):
        matching = [model_id for model_id in capable if model_id.startswith(prefix)]
        if matching:
            return max(matching, key=_version)
    return capable[0] if capable else None


def _usage(usage: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cost": float(usage.get("cost") or 0.0),
    }


def complete_chat(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra: dict[str, Any] | None = None,
    timeout: float | tuple[float, float] = TIMEOUT,
) -> dict[str, Any]:
    """Run one chat completion without streaming, for short answers.

    ``extra`` adds request fields such as ``response_format``, ``provider`` or
    ``reasoning``. Returns ``{"text", "model", "finish_reason", "usage"}``.

    Raises
    ------
    OpenRouterError
        On HTTP and network errors and on errors reported in the body.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "usage": {"include": True},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    payload.update(extra or {})
    response = _send(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=_headers(api_key),
        json=payload,
        timeout=timeout,
    )
    _check(response)
    try:
        body = response.json()
    except ValueError:
        raise OpenRouterError("OpenRouter beklenmeyen bir yanıt döndürdü.") from None
    finally:
        response.close()
    if not isinstance(body, dict):
        raise OpenRouterError("OpenRouter beklenmeyen bir yanıt döndürdü.")
    if body.get("error"):
        raise _stream_error(body["error"])
    choice = (body.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):  # Content parts: keep the text ones.
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return {
        "text": str(content),
        "model": body.get("model") or model,
        "finish_reason": choice.get("finish_reason"),
        "usage": _usage(body.get("usage") or {}),
    }


def _stream_error(error: Any) -> OpenRouterError:
    """Build the error for an ``error`` object inside a stream chunk."""
    code, message = _error_detail({"error": error})
    status = code if isinstance(code, int) else None
    return api_error(status, message)


def _tool_call_event(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") or {}
    arguments = function.get("arguments")
    if arguments is not None and not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {
        "type": "tool_call",
        "index": call.get("index"),
        "id": call.get("id"),
        "name": function.get("name"),
        "arguments": arguments,
    }


def _chunk_events(chunk: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Turn one parsed stream chunk into events."""
    if chunk.get("error"):
        raise _stream_error(chunk["error"])
    choices = chunk.get("choices") or []
    if choices:
        choice = choices[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            yield {"type": "text", "text": delta["content"]}
        for call in delta.get("tool_calls") or []:
            yield _tool_call_event(call)
        if choice.get("finish_reason"):
            yield {"type": "finish", "reason": choice["finish_reason"]}
    usage = chunk.get("usage")
    if usage:
        yield {"type": "usage", **_usage(usage)}


def _sse_data(lines: Iterable[str | bytes]) -> Iterator[str]:
    """Yield the payload of each ``data:`` line until ``[DONE]``.

    Lines are split on bytes and decoded one at a time: decoding first would
    use the wrong charset for ``text/event-stream`` and split on Unicode line
    separators inside JSON strings.
    """
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data:"):
            continue  # blank lines, ": OPENROUTER PROCESSING" comments, other fields
        data = line[5:].strip()
        if data == "[DONE]":
            return
        if data:
            yield data


def stream_chat(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream a chat completion as events.

    Yields ``{"type": "text", "text"}``, ``{"type": "tool_call", "index", "id",
    "name", "arguments"}`` (fragments: later ones carry more of the arguments),
    ``{"type": "finish", "reason"}`` and ``{"type": "usage", "prompt_tokens",
    "completion_tokens", "cost"}``. Closing the generator closes the connection,
    which stops the generation.

    Raises
    ------
    OpenRouterError
        On HTTP and network errors and on errors reported inside the stream.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "usage": {"include": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    response = _send(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=_headers(api_key),
        json=payload,
        stream=True,
        timeout=STREAM_TIMEOUT,
    )
    _check(response)
    try:
        for data in _sse_data(response.iter_lines()):
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if isinstance(chunk, dict):
                yield from _chunk_events(chunk)
    except requests.RequestException as error:
        raise _network_error(error) from error
    finally:
        response.close()
