"""Assistant conversations, one JSON file each under ``<home>/ai/conversations``.

A conversation stores its OpenAI-format messages (without the system prompt),
so it can be sent back to the model as is, and its summed token usage.
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from marketalyzer.ai.agent import parse_arguments, tool_outcome
from marketalyzer.ai.settings import ai_home, write_json
from marketalyzer.paper.models import IST

ID_PATTERN = re.compile(r"^[0-9a-f]{32}\Z")
DEFAULT_TITLE = "Yeni sohbet"
TITLE_LENGTH = 60


def conversations_dir() -> Path:
    """Return the directory holding the conversation files."""
    return ai_home() / "conversations"


def _path(conversation_id: Any) -> Path:
    """Return a conversation's file; reject ids that could escape the directory."""
    if not isinstance(conversation_id, str) or not ID_PATTERN.fullmatch(
        conversation_id
    ):
        raise KeyError(f"Geçersiz sohbet kimliği: {str(conversation_id)[:40]}")
    return conversations_dir() / f"{conversation_id}.json"


def _now() -> str:
    return datetime.now(IST).isoformat()


def message_text(content: Any) -> str:
    """Return the text of a message's content: a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text") or ""
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def new_conversation() -> dict[str, Any]:
    """Return a new, empty and not yet saved conversation."""
    now = _now()
    return {
        "id": uuid.uuid4().hex,
        "title": DEFAULT_TITLE,
        "created": now,
        "updated": now,
        "messages": [],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0},
    }


def _title(messages: list[dict[str, Any]]) -> str | None:
    """Title from the first user message, shortened to TITLE_LENGTH."""
    for message in messages:
        if message.get("role") != "user":
            continue
        text = " ".join(message_text(message.get("content")).split())
        if not text:
            continue
        if len(text) > TITLE_LENGTH:
            text = text[: TITLE_LENGTH - 1].rstrip() + "…"
        return text
    return None


def save_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    """Save a conversation, updating its time and default title; return it."""
    path = _path(conversation.get("id"))
    conversation["updated"] = _now()
    if conversation.get("title", DEFAULT_TITLE) == DEFAULT_TITLE:
        conversation["title"] = (
            _title(conversation.get("messages", [])) or DEFAULT_TITLE
        )
    write_json(path, conversation)
    return conversation


def load_conversation(conversation_id: str) -> dict[str, Any]:
    """Return a saved conversation.

    Raises
    ------
    KeyError
        If the id is invalid or no readable conversation has it.
    """
    path = _path(conversation_id)
    try:
        conversation = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(f"Sohbet bulunamadı: {conversation_id}") from None
    if not isinstance(conversation, dict):
        raise KeyError(f"Sohbet bulunamadı: {conversation_id}")
    return conversation


def list_conversations() -> list[dict[str, Any]]:
    """Return ``{"id", "title", "updated", "messages"}`` summaries, newest first.

    ``messages`` is the number of stored messages. Unreadable files are skipped.
    """
    folder = conversations_dir()
    if not folder.is_dir():
        return []
    summaries = []
    for path in folder.glob("*.json"):
        try:
            conversation = load_conversation(path.stem)
        except KeyError:
            continue
        summaries.append(
            {
                "id": path.stem,
                "title": conversation.get("title") or DEFAULT_TITLE,
                "updated": conversation.get("updated") or "",
                "messages": len(conversation.get("messages") or []),
            }
        )
    summaries.sort(
        key=lambda summary: (summary["updated"], summary["id"]), reverse=True
    )
    return summaries


def delete_conversation(conversation_id: str) -> None:
    """Delete a saved conversation.

    Raises
    ------
    KeyError
        If the id is invalid or no conversation has it.
    """
    path = _path(conversation_id)
    try:
        path.unlink()
    except FileNotFoundError:
        raise KeyError(f"Sohbet bulunamadı: {conversation_id}") from None


def _tool_item(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") or {}
    try:
        args = parse_arguments(function.get("arguments"))
    except ValueError:
        args = {}
    return {
        "role": "tool",
        "id": call.get("id"),
        "name": function.get("name"),
        "args": args,
        "ok": None,
        "result": None,
    }


def display_messages(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn stored messages into items for the chat UI.

    Items are ``{"role": "user" | "assistant", "text"}`` and, for each tool
    call, ``{"role": "tool", "id", "name", "args", "ok", "result"}`` with its
    result merged in (``ok`` and ``result`` stay None if it never ran). System
    messages are skipped.
    """
    items: list[dict[str, Any]] = []
    calls: dict[str, dict[str, Any]] = {}
    for message in conversation.get("messages") or []:
        role = message.get("role")
        if role in ("user", "assistant"):
            text = message_text(message.get("content"))
            if text:
                items.append({"role": role, "text": text})
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                item = _tool_item(call)
                items.append(item)
                calls[str(item["id"])] = item
        elif role == "tool":
            item = calls.get(str(message.get("tool_call_id")))
            if item is not None:
                item["ok"], item["result"] = tool_outcome(message.get("content"))
    return items
