"""The assistant's tool-calling loop.

``run_turn`` sends the conversation to the model, runs the tools it asks for,
feeds the results back and repeats until the model answers in text. Progress
goes to an ``emit`` callback as small JSON-friendly events, for streaming to
the UI.
"""

import json
import math
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from marketalyzer.ai.openrouter import OpenRouterError, stream_chat

MAX_RESULT_CHARS = 16_000
TRUNCATED = "…(kısaltıldı)"
CANCELLED_RESULT = "Kullanıcı durdurdu; araç çalıştırılmadı."
MAX_RAW_ARGUMENTS = 500


class ToolError(Exception):
    """A tool failed in a way the model should hear about, e.g. a bad symbol.

    The message is sent back to the model as the tool result and shown in the
    UI, so it should be Turkish and helpful.
    """


class ToolRegistry(Protocol):
    """The tools the assistant can use."""

    def specs(self) -> list[dict[str, Any]]:
        """Return the tools in OpenAI format.

        Each is ``{"type": "function", "function": {"name", "description",
        "parameters"}}``.
        """
        ...

    def call(self, name: str, args: dict[str, Any]) -> Any:
        """Run a tool and return a JSON-serializable result.

        Raises ``ToolError`` for errors the model should see.
        """
        ...


@dataclass
class TurnResult:
    """What one user turn produced.

    Attributes
    ----------
    messages
        The new OpenAI-format messages: assistant messages (with
        ``tool_calls`` when tools were used) and ``tool`` result messages.
    usage
        Summed ``prompt_tokens``, ``completion_tokens`` and ``cost`` (USD).
    stopped
        True when the user cancelled the turn.
    """

    messages: list[dict[str, Any]]
    usage: dict[str, Any]
    stopped: bool = False


@dataclass
class _Step:
    """One model call: its streamed text and tool calls by index."""

    text: list[str] = field(default_factory=list)
    calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    finish: str | None = None
    cancelled: bool = False
    saved: bool = False


def _never() -> bool:
    return False


def _empty_usage() -> dict[str, Any]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}


def _add_usage(total: dict[str, Any], event: dict[str, Any]) -> None:
    total["prompt_tokens"] += int(event.get("prompt_tokens") or 0)
    total["completion_tokens"] += int(event.get("completion_tokens") or 0)
    total["cost"] += float(event.get("cost") or 0.0)


def _slot_index(calls: dict[int, dict[str, Any]], call_id: str | None) -> int:
    """Index for a fragment without one: match its id, else a new or the last call."""
    for index, slot in calls.items():
        if call_id and slot["id"] == call_id:
            return index
    if not calls:
        return 0
    return max(calls) + 1 if call_id else max(calls)


def _add_fragment(calls: dict[int, dict[str, Any]], event: dict[str, Any]) -> None:
    """Merge a tool-call fragment; argument fragments are concatenated."""
    index = event.get("index")
    if not isinstance(index, int):
        index = _slot_index(calls, event.get("id"))
    slot = calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
    if event.get("id"):
        slot["id"] = event["id"]
    if event.get("name"):
        slot["name"] = event["name"]
    if event.get("arguments"):
        slot["arguments"] += event["arguments"]


def _read_stream(
    events: Iterable[dict[str, Any]],
    step: _Step,
    emit: Callable[[dict[str, Any]], None],
    usage: dict[str, Any],
    cancelled: Callable[[], bool],
) -> None:
    """Consume one model stream into ``step``, forwarding text to the UI."""
    try:
        for event in events:
            kind = event.get("type")
            if kind == "text" and event.get("text"):
                step.text.append(event["text"])
                emit({"type": "text", "text": event["text"]})
            elif kind == "tool_call":
                _add_fragment(step.calls, event)
            elif kind == "finish":
                step.finish = event.get("reason")
            elif kind == "usage":
                _add_usage(usage, event)
            if cancelled():
                step.cancelled = True
                break
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()


def _tool_calls(step: _Step) -> list[dict[str, Any]]:
    """Return the step's complete tool calls in OpenAI format, ids filled in."""
    return [
        {
            "id": slot["id"] or f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
        }
        for _, slot in sorted(step.calls.items())
        if slot["name"]
    ]


def _assistant_message(
    text: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if tool_calls:
        return {"role": "assistant", "content": text or None, "tool_calls": tool_calls}
    if text:
        return {"role": "assistant", "content": text}
    return None


def parse_arguments(arguments: str | None) -> dict[str, Any]:
    """Parse a tool call's JSON arguments; empty arguments mean no arguments.

    Raises
    ------
    ValueError
        If the arguments are not a JSON object.
    """
    if not arguments or not arguments.strip():
        return {}
    value = json.loads(arguments)
    if not isinstance(value, dict):
        raise ValueError("argümanlar bir JSON nesnesi olmalı")
    return value


def _finite(value: Any) -> Any:
    """Replace NaN and infinities, which are not valid JSON, with None."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def tool_content(result: Any) -> str:
    """Serialize a tool result for the model, shortened past MAX_RESULT_CHARS."""
    try:
        content = json.dumps(_finite(result), ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        content = json.dumps(str(result), ensure_ascii=False)
    if len(content) > MAX_RESULT_CHARS:
        content = content[:MAX_RESULT_CHARS] + TRUNCATED
    return content


def tool_outcome(content: str) -> tuple[bool, Any]:
    """Return ``(ok, result)`` of a stored tool message's content.

    An ``{"error": message}`` object is a failure whose result is the message;
    content that is not JSON (a shortened result) is returned as text.
    """
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return True, content
    if isinstance(value, dict) and set(value) == {"error"}:
        return False, value["error"]
    return True, value


def _call_tool(tools: ToolRegistry, names: set[str], name: str, args: dict) -> Any:
    """Run a tool; errors become ``{"error": message}`` results."""
    if name not in names:
        return {"error": f"Bilinmeyen araç: {name}"}
    try:
        return tools.call(name, args)
    except ToolError as error:
        return {"error": str(error)}
    except Exception as error:  # A broken tool must not end the turn.
        return {"error": f"Araç hatası: {type(error).__name__}: {error}"}


def _run_tool(
    tools: ToolRegistry,
    names: set[str],
    call: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Run one tool call, emitting tool_start and tool_end; return the tool message."""
    call_id, name = call["id"], call["function"]["name"]
    raw = call["function"]["arguments"]
    try:
        args = parse_arguments(raw)
        result = None
    except ValueError as error:
        args = {}
        result = {
            "error": "Geçersiz araç argümanları (JSON nesnesi bekleniyordu):"
            f" {error}. Gönderilen: {raw[:MAX_RAW_ARGUMENTS]}"
        }
        # The call is part of the assistant message already in the history;
        # providers may reject a history whose arguments are not valid JSON.
        call["function"]["arguments"] = "{}"
    emit({"type": "tool_start", "id": call_id, "name": name, "args": args})
    if result is None:
        result = _call_tool(tools, names, name, args)
    content = tool_content(result)
    ok, shown = tool_outcome(content)
    emit({"type": "tool_end", "id": call_id, "name": name, "ok": ok, "result": shown})
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _skipped(call: dict[str, Any]) -> dict[str, Any]:
    """Return the result of a tool call skipped because the user stopped."""
    content = tool_content({"error": CANCELLED_RESULT})
    return {"role": "tool", "tool_call_id": call["id"], "content": content}


def _notice(emit: Callable[[dict[str, Any]], None], text: str) -> None:
    emit({"type": "notice", "text": text})


def _save_step(
    step: _Step, new: list[dict[str, Any]], emit: Callable[[dict[str, Any]], None]
) -> list[dict[str, Any]]:
    """Add the step's assistant message to ``new``; return the calls to run.

    A cancelled step keeps its partial text but drops its tool calls, whose
    arguments may be incomplete.
    """
    calls = [] if step.cancelled else _tool_calls(step)
    message = _assistant_message("".join(step.text), calls)
    step.saved = True
    if message is not None:
        new.append(message)
    if step.cancelled:
        return []
    if step.finish == "length":
        _notice(emit, "Yanıt, modelin çıktı sınırına ulaştığı için kesildi.")
    if message is None:
        _notice(
            emit,
            "Model boş bir yanıt döndürdü. Tekrar deneyin veya başka bir model seçin.",
        )
    return calls


def _run_tools(
    tools: ToolRegistry,
    names: set[str],
    calls: list[dict[str, Any]],
    new: list[dict[str, Any]],
    emit: Callable[[dict[str, Any]], None],
    cancelled: Callable[[], bool],
) -> bool:
    """Run the calls in order, adding their results to ``new``.

    Return True if the user cancelled; the calls not run yet then get a
    "stopped" result, as every tool call needs one.
    """
    for position, call in enumerate(calls):
        if cancelled():
            new.extend(_skipped(skipped) for skipped in calls[position:])
            return True
        new.append(_run_tool(tools, names, call, emit))
    return False


def run_turn(
    history: list[dict[str, Any]],
    *,
    api_key: str,
    model: str,
    tools: ToolRegistry,
    system_prompt: str,
    emit: Callable[[dict[str, Any]], None],
    max_steps: int = 12,
    cancelled: Callable[[], bool] = _never,
    stream: Callable[..., Iterable[dict[str, Any]]] = stream_chat,
) -> TurnResult:
    """Answer the last user message in ``history``, using tools as needed.

    ``history`` is the OpenAI-format conversation ending with the new user
    message; the system prompt is added here. Each step streams one model
    reply; when it asks for tools they run and the loop continues, up to
    ``max_steps`` model calls. ``stream`` is called as ``stream(api_key, model,
    messages, tools)`` and yields ``stream_chat`` events.

    Events passed to ``emit``: ``{"type": "text", "text"}``, ``{"type":
    "tool_start", "id", "name", "args"}``, ``{"type": "tool_end", "id", "name",
    "ok", "result"}``, ``{"type": "notice", "text"}`` and finally ``{"type":
    "usage", "prompt_tokens", "completion_tokens", "cost"}``.

    Raises
    ------
    OpenRouterError
        When the model call fails. ``error.partial`` is a TurnResult with the
        messages completed before the failure, which should still be saved.
    """
    specs = tools.specs()
    names = {spec["function"]["name"] for spec in specs}
    new: list[dict[str, Any]] = []
    usage = _empty_usage()
    stopped = False
    step = _Step()
    try:
        for _ in range(max_steps):
            if cancelled():
                stopped = True
                break
            step = _Step()
            messages = [{"role": "system", "content": system_prompt}, *history, *new]
            _read_stream(
                stream(api_key, model, messages, specs or None),
                step,
                emit,
                usage,
                cancelled,
            )
            calls = _save_step(step, new, emit)
            if not calls:
                stopped = step.cancelled
                break
            stopped = _run_tools(tools, names, calls, new, emit, cancelled)
            if stopped:
                break
        else:
            _notice(
                emit,
                f"Adım sınırına ulaşıldı ({max_steps} model çağrısı); yanıt yarım"
                " kalmış olabilir. Devam etmesi için 'devam et' yazabilirsiniz.",
            )
    except OpenRouterError as error:
        partial = list(new)
        if not step.saved and step.text:
            partial.append({"role": "assistant", "content": "".join(step.text)})
        error.partial = TurnResult(partial, usage, stopped=False)
        raise
    emit({"type": "usage", **usage})
    return TurnResult(new, usage, stopped)
