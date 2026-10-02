import json
import os
import stat

import pytest

from marketalyzer.ai import settings as settings_module
from marketalyzer.ai.settings import (
    AISettings,
    load_settings,
    public_settings,
    save_settings,
    settings_path,
)

KEY = "sk-or-v1-0123456789abcdef0123456789abcdefWXYZ"
# Windows has no POSIX permission bits; chmod only toggles read-only there.
POSIX = os.name == "posix"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MARKETALYZER_AI_MODEL", raising=False)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_defaults_without_a_file(tmp_path):
    assert settings_path() == tmp_path / "home" / "ai" / "settings.json"
    assert load_settings() == AISettings()


def test_save_and_load(tmp_path):
    saved = save_settings(
        api_key=f"  {KEY}\n", model="anthropic/claude-sonnet-4.5", allow_trading=True
    )
    assert saved.api_key == KEY
    assert saved.key_source == "file"
    loaded = load_settings()
    assert loaded == saved
    assert loaded.model == "anthropic/claude-sonnet-4.5"
    assert loaded.allow_trading is True
    assert KEY not in repr(loaded)


def test_file_is_private_and_written_atomically():
    save_settings(api_key=KEY)
    path = settings_path()
    if POSIX:
        assert mode(path) == 0o600
        assert mode(path.parent) == 0o700
    save_settings(model="openai/gpt-5")
    if POSIX:
        assert mode(path) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


def test_only_passed_fields_change():
    save_settings(api_key=KEY, model="a/b", allow_trading=True)
    assert save_settings(model="c/d").api_key == KEY
    current = save_settings(api_key=None, allow_trading=False)
    assert (current.api_key, current.model, current.allow_trading) == (
        KEY,
        "c/d",
        False,
    )
    assert save_settings(model="").model is None


def test_clear_key():
    save_settings(api_key=KEY, model="a/b")
    cleared = save_settings(clear_key=True)
    assert cleared.api_key is None
    assert cleared.key_source is None
    assert cleared.model == "a/b"
    assert "api_key" not in json.loads(settings_path().read_text())


@pytest.mark.parametrize("bad", ["", "   ", "sk-or v1", "sk-or-\x00", "x" * 201])
def test_invalid_key_is_rejected(bad):
    with pytest.raises(ValueError, match="API anahtarı"):
        save_settings(api_key=bad)
    assert not settings_path().exists()


def test_invalid_model_is_rejected():
    with pytest.raises(ValueError, match="model"):
        save_settings(model="bad model")


def test_environment_overrides_the_file(monkeypatch):
    save_settings(api_key=KEY, model="a/b")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-environment-1234")
    monkeypatch.setenv("MARKETALYZER_AI_MODEL", "x/y")
    current = load_settings()
    assert current.api_key == "sk-or-v1-from-environment-1234"
    assert current.key_source == "env"
    assert current.model == "x/y"
    # Saving other fields does not copy the environment key into the file.
    save_settings(allow_trading=True)
    assert json.loads(settings_path().read_text())["api_key"] == KEY


def test_empty_environment_key_is_ignored(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "  ")
    assert load_settings().key_source is None


def test_corrupt_file_gives_defaults():
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert load_settings() == AISettings()
    path.write_text('["a list"]')
    assert load_settings() == AISettings()
    assert save_settings(model="a/b").model == "a/b"


def test_public_settings_never_include_the_key(monkeypatch):
    shown = public_settings(save_settings(api_key=KEY, model="a/b"))
    assert shown == {
        "configured": True,
        "key_hint": "sk-or-…WXYZ",
        "key_source": "file",
        "model": "a/b",
        "allow_trading": False,
        "decision_model": None,
    }
    assert KEY not in json.dumps(shown)
    assert public_settings(AISettings())["key_hint"] is None
    assert public_settings(AISettings())["configured"] is False
    # A short key is not revealed through its "last four" characters.
    assert public_settings(AISettings(api_key="abcd"))["key_hint"] == "sk-or-…"


def test_failed_write_leaves_no_temp_file(monkeypatch):
    save_settings(api_key=KEY)

    def broken_replace(*args):
        raise OSError("disk full")

    monkeypatch.setattr(settings_module.os, "replace", broken_replace)
    with pytest.raises(OSError):
        save_settings(model="a/b")
    assert [p.name for p in settings_path().parent.iterdir()] == ["settings.json"]
    assert load_settings().model is None


def test_decision_model(monkeypatch):
    assert save_settings(decision_model="gemini-flash").decision_model == "gemini-flash"
    assert load_settings().decision_model == "gemini-flash"
    assert save_settings(decision_model="").decision_model is None
    monkeypatch.setenv("MARKETALYZER_DECISION_MODEL", "x/y")
    assert load_settings().decision_model == "x/y"
    with pytest.raises(ValueError):
        save_settings(decision_model="bad model")
