"""AI assistant settings: the provider and its API key, the model and trading access.

Settings live in ``<home>/ai/settings.json``, readable only by the owner. The
provider is OpenRouter or fal.ai, which serves the same models from its own
account; each keeps its own key and the chosen one is used everywhere. Without a
choice, OpenRouter is used unless only a fal.ai key is set. The
``OPENROUTER_API_KEY``, ``FAL_KEY`` and ``MARKETALYZER_AI_MODEL`` environment
variables override the file. Keys are never logged or returned to the UI. The
decision model, used for fast buy/sell decisions, is a preset key such as
``claude-haiku`` or any OpenRouter model id.
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marketalyzer.ai.openrouter import FalKey
from marketalyzer.paper.cli import default_home

KEY_ENV = "OPENROUTER_API_KEY"
FAL_KEY_ENV = "FAL_KEY"
PROVIDERS = ("openrouter", "fal")
# Each provider's field in the settings file and the variable that overrides it.
KEY_FIELDS = {"openrouter": ("api_key", KEY_ENV), "fal": ("fal_key", FAL_KEY_ENV)}
HINT_PREFIX = {"openrouter": "sk-or-", "fal": "fal-"}
MODEL_ENV = "MARKETALYZER_AI_MODEL"
DECISION_MODEL_ENV = "MARKETALYZER_DECISION_MODEL"
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
        The chosen provider's key (a ``FalKey`` for fal.ai), or None when that
        provider has no key.
    model
        The OpenRouter model id, or None to pick a default.
    allow_trading
        Whether the assistant may place and cancel paper orders.
    decision_model
        The preset key or model id for fast decisions, or None for the default.
    key_source
        Where the chosen key came from: "env", "file" or None.
    provider
        The chosen provider: "openrouter" or "fal".
    keys
        Every configured key as ``{provider: (key, source)}``.
    """

    api_key: str | None = field(default=None, repr=False)
    model: str | None = None
    allow_trading: bool = False
    key_source: str | None = None
    decision_model: str | None = None
    provider: str = "openrouter"
    keys: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)


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
    keys: dict[str, tuple[str, str]] = {}
    for provider, (name, env) in KEY_FIELDS.items():
        env_key = os.environ.get(env, "").strip()
        stored_key = _text(stored.get(name))
        if env_key:
            keys[provider] = (env_key, "env")
        elif stored_key:
            keys[provider] = (stored_key, "file")
    provider = stored.get("provider")
    if provider not in PROVIDERS:
        provider = "fal" if list(keys) == ["fal"] else "openrouter"
    api_key, key_source = keys.get(provider, (None, None))
    if api_key and provider == "fal":
        api_key = FalKey(api_key)
    settings = AISettings(
        api_key=api_key,
        model=_text(stored.get("model")),
        allow_trading=stored.get("allow_trading") is True,
        key_source=key_source,
        decision_model=_text(stored.get("decision_model")),
        provider=provider,
        keys=keys,
    )
    env_model = os.environ.get(MODEL_ENV, "").strip()
    if env_model:
        settings.model = env_model
    env_decision = os.environ.get(DECISION_MODEL_ENV, "").strip()
    if env_decision:
        settings.decision_model = env_decision
    return settings


def _check_key(api_key: str, provider: str = "openrouter") -> str:
    key = api_key.strip()
    if not key:
        raise ValueError("API anahtarı boş olamaz.")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(
            "API anahtarı çok uzun; anahtarı doğru kopyaladığınızdan emin olun."
        )
    if any(char.isspace() for char in key) or not (key.isascii() and key.isprintable()):
        raise ValueError(
            "API anahtarı geçersiz karakterler içeriyor; anahtarlar boşluksuz olmalı"
            " (OpenRouter: sk-or-v1-..., fal.ai: fal_sk_...:...)."
        )
    # fal.ai keys are "fal_sk_...:..." or "<id>:<secret>"; OpenRouter's never
    # contain a colon.
    if provider == "openrouter" and (key.startswith("fal_") or ":" in key):
        raise ValueError(
            "Bu bir fal.ai anahtarı gibi görünüyor; sağlayıcı olarak fal.ai'yi seçip"
            " fal.ai anahtarı alanına girin."
        )
    if provider == "fal" and key.startswith("sk-or-"):
        raise ValueError(
            "Bu bir OpenRouter anahtarı; sağlayıcı olarak OpenRouter'ı seçip"
            " OpenRouter anahtarı alanına girin."
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
    decision_model: str | None = _UNSET,
    clear_key: bool = False,
    provider: str | None = _UNSET,
    fal_key: str | None = _UNSET,
    clear_fal_key: bool = False,
) -> AISettings:
    """Change the stored settings and return the effective ones.

    Only the arguments passed change. ``api_key`` is the OpenRouter key and
    ``fal_key`` the fal.ai key; None keeps the stored one (``clear_key`` and
    ``clear_fal_key`` remove them). ``provider`` chooses which one is used.
    ``model=None`` or an empty model clears the model so a default is picked.
    Environment overrides are not written to the file.

    Raises
    ------
    ValueError
        If a key, the provider or the model does not look valid. The message
        is Turkish, for the UI.
    """
    stored = _stored()
    if provider is not _UNSET and provider is not None:
        if provider not in PROVIDERS:
            raise ValueError(f"Bilinmeyen sağlayıcı: {str(provider)[:40]}")
        stored["provider"] = provider
    changes = {"openrouter": (api_key, clear_key), "fal": (fal_key, clear_fal_key)}
    for name, (key, clear) in changes.items():
        field_name = KEY_FIELDS[name][0]
        if clear:
            stored.pop(field_name, None)
        if key is not _UNSET and key is not None:
            stored[field_name] = _check_key(key, name)
    if model is not _UNSET:
        checked = _check_model(model)
        if checked is None:
            stored.pop("model", None)
        else:
            stored["model"] = checked
    if allow_trading is not _UNSET:
        stored["allow_trading"] = bool(allow_trading)
    if decision_model is not _UNSET:
        checked = _check_model(decision_model)
        if checked is None:
            stored.pop("decision_model", None)
        else:
            stored["decision_model"] = checked
    write_json(settings_path(), stored)
    return load_settings()


def key_hint(api_key: str | None, provider: str = "openrouter") -> str | None:
    """Return a hint that identifies a key without revealing it."""
    if not api_key:
        return None
    tail = api_key[-4:] if len(api_key) >= 12 else ""
    return f"{HINT_PREFIX[provider]}…{tail}"


def _key_view(api_key: str | None, source: str | None, provider: str) -> dict:
    return {
        "configured": bool(api_key),
        "key_hint": key_hint(api_key, provider),
        "key_source": source,
    }


def public_settings(settings: AISettings) -> dict[str, Any]:
    """Return the settings that are safe to show in the UI, without the keys.

    ``configured``, ``key_hint`` and ``key_source`` describe the chosen
    provider's key; ``providers`` describes each provider's.
    """
    providers = {
        provider: _key_view(*settings.keys.get(provider, (None, None)), provider)
        for provider in PROVIDERS
    }
    return {
        "provider": settings.provider,
        **_key_view(settings.api_key, settings.key_source, settings.provider),
        "providers": providers,
        "model": settings.model,
        "allow_trading": settings.allow_trading,
        "decision_model": settings.decision_model,
    }
