"""Run assistant turns in background threads and stream their events.

Each turn runs in its own thread so the web server can stream events as
Server-Sent Events while tools (backtests, optimizations) do their work. A turn
can be stopped from the UI; whatever finished before is still saved.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from marketalyzer.ai.agent import run_turn
from marketalyzer.ai.conversations import (
    load_conversation,
    new_conversation,
    save_conversation,
)
from marketalyzer.ai.openrouter import (
    OpenRouterError,
    list_models,
    pick_default_model,
    stream_chat,
)
from marketalyzer.ai.prompt import system_prompt
from marketalyzer.ai.settings import load_settings
from marketalyzer.ai.tools import Tools

MAX_HISTORY_CHARS = 150_000
MAX_MESSAGE_CHARS = 8_000


class ChatError(Exception):
    """A request the chat cannot start, with an HTTP status for the API."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _size(message: dict[str, Any]) -> int:
    content = message.get("content") or ""
    calls = message.get("tool_calls") or []
    return len(str(content)) + sum(len(str(call)) for call in calls)


def trim_history(
    messages: list[dict[str, Any]], limit: int = MAX_HISTORY_CHARS
) -> list[dict[str, Any]]:
    """Drop the oldest exchanges so the history fits in ``limit`` characters.

    Cuts only before a user message, so tool calls keep their results.
    """
    total = 0
    start = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        total += _size(messages[index])
        if messages[index].get("role") == "user":
            if total > limit and start < len(messages):
                break
            start = index
    return messages[start:]


def resolve_model(api_key: str, model: str | None) -> str:
    """Return the chosen model, or pick a tool-capable default from OpenRouter."""
    if model:
        return model
    chosen = pick_default_model(list_models(api_key))
    if not chosen:
        raise ChatError(
            "Araç kullanabilen bir model bulunamadı; Ayarlar'dan model seçin."
        )
    return chosen


class ChatRunner:
    """Start, stream and stop assistant turns, one at a time per conversation."""

    def __init__(self, paper_path: Path, demo: bool = False, stream=stream_chat):
        self.paper_path = paper_path
        self.demo = demo
        self.stream = stream
        self._running: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def running(self, conversation_id: str) -> bool:
        """Return whether a turn is running in the conversation."""
        return conversation_id in self._running

    def stop(self, conversation_id: str) -> bool:
        """Ask a running turn to stop; return whether one was running."""
        event = self._running.get(conversation_id)
        if event is None:
            return False
        event.set()
        return True

    def start(
        self,
        message: str,
        emit: Callable[[dict[str, Any]], None],
        conversation_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], threading.Event]:
        """Start a turn in a thread; events go to ``emit``, ending with ``done``.

        Raises ``ChatError`` if the turn cannot start (no key, busy, ...).
        """
        message = message.strip()
        if not message:
            raise ChatError("Mesaj boş olamaz.")
        if len(message) > MAX_MESSAGE_CHARS:
            raise ChatError(f"Mesaj en fazla {MAX_MESSAGE_CHARS} karakter olabilir.")
        settings = load_settings()
        if not settings.api_key:
            raise ChatError(
                "API anahtarı ayarlanmamış. Ayarlar > Yapay zeka bölümünden OpenRouter"
                " ya da fal.ai anahtarınızı ekleyin."
            )
        if conversation_id:
            try:
                conversation = load_conversation(conversation_id)
            except KeyError:
                raise ChatError("Sohbet bulunamadı.", 404) from None
        else:
            conversation = new_conversation()
        cancel = threading.Event()
        with self._lock:
            if conversation["id"] in self._running:
                raise ChatError("Bu sohbette bir yanıt zaten hazırlanıyor.", 409)
            self._running[conversation["id"]] = cancel
        thread = threading.Thread(
            target=self._run,
            args=(conversation, message, settings, context, emit, cancel),
            daemon=True,
            name=f"chat-{conversation['id'][:8]}",
        )
        thread.start()
        return conversation, cancel

    def _run(self, conversation, message, settings, context, emit, cancel) -> None:
        user = {"role": "user", "content": message}
        new: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        stopped = False
        model = settings.model
        try:
            model = resolve_model(settings.api_key, settings.model)
            emit({"type": "model", "model": model})
            tools = Tools(self.paper_path, self.demo, on_event=emit)
            result = run_turn(
                [*trim_history(conversation["messages"]), user],
                api_key=settings.api_key,
                model=model,
                tools=tools,
                system_prompt=system_prompt(self.demo, context),
                emit=emit,
                cancelled=cancel.is_set,
                stream=self.stream,
            )
            new, usage, stopped = result.messages, result.usage, result.stopped
        except OpenRouterError as error:
            if error.partial is not None:
                new, usage = error.partial.messages, error.partial.usage
            emit({"type": "error", "message": error.message})
        except ChatError as error:
            emit({"type": "error", "message": error.message})
        except Exception as error:  # Report anything else instead of hanging the UI.
            emit({"type": "error", "message": f"Beklenmeyen hata: {error}"})
        finally:
            try:
                conversation["messages"].extend([user, *new])
                total = conversation.setdefault("usage", {})
                for key in ("prompt_tokens", "completion_tokens", "cost"):
                    total[key] = (total.get(key) or 0) + (usage.get(key) or 0)
                conversation["model"] = model
                save_conversation(conversation)
            finally:
                with self._lock:
                    self._running.pop(conversation["id"], None)
                emit(
                    {
                        "type": "done",
                        "stopped": stopped,
                        "conversation_id": conversation["id"],
                        "title": conversation.get("title"),
                        "usage": conversation.get("usage"),
                    }
                )
