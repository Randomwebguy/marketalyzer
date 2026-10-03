"""Decisions through the local Claude Code command line (a fake one here)."""

import json
import subprocess

import pytest

from marketalyzer.ai import claude_cli
from marketalyzer.ai.openrouter import OpenRouterError, account_blocked


class FakeCli:
    """Stands in for ``subprocess.run``; answers like ``claude -p --output-format json``."""

    def __init__(self, result="{}", error=False, status=None):
        self.calls = []
        self.reply = {
            "type": "result",
            "is_error": error,
            "api_error_status": status,
            "result": result,
            "total_cost_usd": 0.0021,
            "usage": {
                "input_tokens": 900,
                "cache_read_input_tokens": 100,
                "output_tokens": 30,
            },
            "modelUsage": {"claude-haiku-4-5": {}},
        }

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(self.reply), "")


MESSAGES = [
    {"role": "system", "content": "Yalnızca JSON yaz."},
    {"role": "user", "content": '{"görünüm": "ğüşıöç"}'},
]


def test_a_request_runs_claude_print_without_the_users_key(monkeypatch):
    fake = FakeCli('{"karar": "AL", "guven": 70, "gerekce": "test"}')
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "user-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:1")
    answer = claude_cli.complete(claude_cli.KEY, "haiku", MESSAGES, temperature=0.0)
    args, kwargs = fake.calls[0]
    assert args[1] == "-p" and args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == "project"
    assert args[args.index("--system-prompt") + 1] == "Yalnızca JSON yaz."
    assert "--no-session-persistence" in args and "--strict-mcp-config" in args
    assert kwargs["input"] == '{"görünüm": "ğüşıöç"}' and kwargs["encoding"] == "utf-8"
    assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"} & set(kwargs["env"])
    assert kwargs["env"]["MAX_THINKING_TOKENS"] == "0"
    assert answer["text"].startswith('{"karar"')
    assert answer["model"] == "claude-haiku-4-5"
    assert answer["usage"]["cost"] == 0.0 and answer["usage"]["prompt_tokens"] == 1000


def test_signed_out_or_used_up_plans_stop_the_test(monkeypatch):
    for text, status in (
        ("Not logged in · Please run /login", 401),
        ("Claude AI usage limit reached|1759500000", 402),
        ("Overloaded", 529),
    ):
        monkeypatch.setattr(
            claude_cli.subprocess, "run", FakeCli(text, error=True, status=status)
        )
        with pytest.raises(OpenRouterError) as caught:
            claude_cli.complete(claude_cli.KEY, "haiku", MESSAGES)
        assert caught.value.status == status
        assert account_blocked(caught.value) == (status in (401, 402))


def test_a_missing_command_line_is_reported(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(claude_cli.subprocess, "run", missing)
    with pytest.raises(OpenRouterError, match="bulunamadı") as caught:
        claude_cli.complete(claude_cli.KEY, "haiku", MESSAGES)
    assert caught.value.status == 401


def test_the_decider_and_the_coach_use_the_command_line(monkeypatch, tmp_path):
    fake = FakeCli('{"karar": "BEKLE", "guven": 55, "gerekce": "zayıf"}')
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    decision = claude_cli.decider(use_cache=False).decide(
        {"pozisyon": {"durum": "yok"}}
    )
    assert decision.label == "BEKLE" and decision.cost == 0.0 and not decision.error
    assert fake.calls[0][0][fake.calls[0][0].index("--model") + 1] == "haiku"
    coach = claude_cli.coach()
    assert coach.model == "sonnet" and coach.complete is claude_cli.complete
