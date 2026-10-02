"""AI assistant settings: the OpenRouter API key, the model and trading access.

Settings live in ``<home>/ai/settings.json``, readable only by the owner. The
``OPENROUTER_API_KEY`` and ``MARKETALYZER_AI_MODEL`` environment variables
override the file. The key is never logged or returned to the UI.
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marketalyzer.paper.cli import default_home

KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "MARKETALYZER_AI_MODEL"
MAX_KEY_LENGTH = 200
MAX_MODEL_LENGTH = 200
# Marks a save_settings argument that was not passed.
_UNSET: Any = object()


@dataclass
class AISettings:
    """The assistant's settings.

    Attributes
    ----------
    api_key
        The OpenRouter API key, or None when none is configured.
    model
        The OpenRouter model id, or None to pick a default.
    allow_trading
        Whether the assistant may place and cancel paper orders.
    key_source
        Where the key came from: "env", "file" or None.
    """

    api_key: str | None = field(default=None, repr=False)
    model: str | None = None
    allow_trading: bool = False
    key_source: str | None = None


def ai_home() -> Path:
    """Return the directory holding the assistant's settings and conversations."""
    return default_home() / "ai"


def settings_path() -> Path:
    """Return the settings file."""
    return ai_home() / "settings.json"


def private_dir(path: Path) -> Path:
    """Create a directory (and its parents) and make it accessible to the owner only."""
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_json(path: Path, data: Any) -> None:
    """Write JSON atomically to a file only the owner can read.

    The data goes to a temporary file in the same directory, which then
    replaces the target, so readers never see a half-written file.
    """
    private_dir(path.parent)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _stored() -> dict[str, Any]:
    """Return the settings file's contents; an unreadable file counts as empty."""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def load_settings() -> AISettings:
    """Return the settings from the file, overridden by the environment."""
    stored = _stored()
    settings = AISettings(
        api_key=_text(stored.get("api_key")),
        model=_text(stored.get("model")),
        allow_trading=stored.get("allow_trading") is True,
    )
    if settings.api_key:
        settings.key_source = "file"
    env_key = os.environ.get(KEY_ENV, "").strip()
    if env_key:
        settings.api_key, settings.key_source = env_key, "env"
    env_model = os.environ.get(MODEL_ENV, "").strip()
    if env_model:
        settings.model = env_model
    return settings


def _check_key(api_key: str) -> str:
    key = api_key.strip()
    if not key:
        raise ValueError("API anahtarı boş olamaz.")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(
            "API anahtarı çok uzun; anahtarı doğru kopyaladığınızdan emin olun."
        )
    if any(char.isspace() for char in key) or not (key.isascii() and key.isprintable()):
        raise ValueError(
            "API anahtarı geçersiz karakterler içeriyor; OpenRouter anahtarları"
            " boşluksuz olmalı (ör. sk-or-v1-...)."
        )
    return key


def _check_model(model: str | None) -> str | None:
    if model is None or not model.strip():
        return None
    model = model.strip()
    if len(model) > MAX_MODEL_LENGTH or any(char.isspace() for char in model):
        raise ValueError(f"Geçersiz model adı: {model[:80]}")
    return model


def save_settings(
    *,
    api_key: str | None = _UNSET,
    model: str | None = _UNSET,
    allow_trading: bool = _UNSET,
    clear_key: bool = False,
) -> AISettings:
    """Change the stored settings and return the effective ones.

    Only the arguments passed change. ``api_key=None`` keeps the stored key
    (use ``clear_key`` to remove it), ``model=None`` or an empty model clears
    the model so a default is picked. Environment overrides are not written
    to the file.

    Raises
    ------
    ValueError
        If the key or the model does not look valid. The message is Turkish,
        for the UI.
    """
    stored = _stored()
    if clear_key:
        stored.pop("api_key", None)
    if api_key is not _UNSET and api_key is not None:
        stored["api_key"] = _check_key(api_key)
    if model is not _UNSET:
        checked = _check_model(model)
        if checked is None:
            stored.pop("model", None)
        else:
            stored["model"] = checked
    if allow_trading is not _UNSET:
        stored["allow_trading"] = bool(allow_trading)
    write_json(settings_path(), stored)
    return load_settings()


def key_hint(api_key: str | None) -> str | None:
    """Return a hint that identifies a key without revealing it."""
    if not api_key:
        return None
    tail = api_key[-4:] if len(api_key) >= 12 else ""
    return f"sk-or-…{tail}"


def public_settings(settings: AISettings) -> dict[str, Any]:
    """Return the settings that are safe to show in the UI, without the key."""
    return {
        "configured": bool(settings.api_key),
        "key_hint": key_hint(settings.api_key),
        "key_source": settings.key_source,
        "model": settings.model,
        "allow_trading": settings.allow_trading,
    }
