"""Ask Claude through the local Claude Code command line, for development.

Blind tests run during development can take their decisions and lessons from
the person's Claude plan instead of paid API credit. Each request starts
``claude -p`` with the request's system prompt, no tools, no MCP servers and
no saved session. Only project settings are loaded and the Anthropic key
variables are dropped, so an API key or gateway set in the user's own Claude
Code settings is neither used nor changed. The command line must be signed
in once with ``claude auth login``.

``complete`` has the signature of ``openrouter.complete_chat``, so a
``Decider`` or a ``learn.Coach`` takes it as its ``complete``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from marketalyzer.ai.decide import Decider, DecisionCache
from marketalyzer.ai.learn import Coach
from marketalyzer.ai.openrouter import OpenRouterError

KEY = "claude-code"  # stands in for an API key: the command line signs in itself
DECISION_MODEL = "haiku"
COACH_MODEL = "sonnet"
TIMEOUT = 180.0
# Variables that would send the request to a key or a gateway instead of the plan.
DROPPED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


def command() -> str:
    """Return the Claude Code executable (``MARKETALYZER_CLAUDE_CLI`` or ``claude``)."""
    return os.environ.get("MARKETALYZER_CLAUDE_CLI", "claude")


def _workdir() -> Path:
    # An empty folder: no project settings, memory or CLAUDE.md come along.
    path = Path(tempfile.gettempdir()) / "marketalyzer-claude"
    path.mkdir(exist_ok=True)
    return path


def _status(text: str, reported: Any) -> int | None:
    """Map a failure to the status the deciders understand.

    Not signed in counts as a bad key and a used-up plan as no credit: both
    stop a blind test, since every later request would fail too.
    """
    lowered = text.lower()
    if "not logged in" in lowered or "/login" in lowered or "authenticat" in lowered:
        return 401
    if "limit" in lowered and ("usage" in lowered or "reached" in lowered):
        return 402
    return reported if isinstance(reported, int) else None


def complete(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra: dict[str, Any] | None = None,
    timeout: Any = None,
) -> dict[str, Any]:
    """Answer ``messages`` with ``claude -p``; return text, model and usage.

    Temperature, token limits and provider extras do not apply to the command
    line and are ignored. The cost is 0: the plan's usage limits apply instead
    (``list_cost`` keeps the command line's list-price figure).
    """
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    prompt = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
    args = [
        command(), "-p", "--output-format", "json", "--model", model,
        "--tools", "", "--no-session-persistence", "--strict-mcp-config",
        "--disable-slash-commands", "--setting-sources", "project",
    ]  # fmt: skip
    if system:
        args += ["--system-prompt", system]
    env = {k: v for k, v in os.environ.items() if k not in DROPPED}
    try:
        done = subprocess.run(  # noqa: S603 - fixed program, no shell
            args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT,
            env=env,
            cwd=_workdir(),
            check=False,
        )
    except FileNotFoundError as error:
        raise OpenRouterError(
            "Claude Code komut satırı bulunamadı; kurun ya da"
            " MARKETALYZER_CLAUDE_CLI ile yolunu verin.",
            401,
        ) from error
    except subprocess.TimeoutExpired as error:
        raise OpenRouterError("Claude Code yanıt vermedi (zaman aşımı).") from error
    try:
        data = json.loads(done.stdout)
    except ValueError as error:
        detail = " ".join((done.stderr or done.stdout or "").split())[:300]
        raise OpenRouterError(f"Claude Code yanıtı okunamadı: {detail}") from error
    if data.get("is_error"):
        text = " ".join(str(data.get("result") or "bilinmeyen hata").split())
        raise OpenRouterError(
            f"Claude Code hata verdi: {text}",
            _status(text, data.get("api_error_status")),
        )
    usage = data.get("usage") or {}
    prompt_tokens = sum(
        int(usage.get(key) or 0)
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    return {
        "text": str(data.get("result") or ""),
        "model": next(iter(data.get("modelUsage") or {}), model),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "cost": 0.0,
            "list_cost": data.get("total_cost_usd"),
        },
    }


def decider(model: str = DECISION_MODEL, use_cache: bool = True) -> Decider:
    """Return a decider that asks Claude through the command line."""
    return Decider(
        KEY, model, cache=DecisionCache() if use_cache else None, complete=complete
    )


def coach(model: str = COACH_MODEL) -> Coach:
    """Return a coach that writes rules through the command line."""
    return Coach(KEY, model, complete=complete)
