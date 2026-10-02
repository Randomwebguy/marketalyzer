"""Store scripts on disk next to the ready-made script library.

User scripts live in ``$MARKETALYZER_HOME/scripts/<name>.pine``. The library
ships with the package; saving a script under a library name keeps a private
copy that takes precedence, and deleting it brings the original back.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from marketalyzer.paper.cli import default_home
from marketalyzer.scripting.errors import ScriptError
from marketalyzer.scripting.parser import MAX_SOURCE
from marketalyzer.scripting.runtime import Script, compile_script
from marketalyzer.scripting.strategy import ScriptStrategy, script_strategy

LIBRARY = Path(__file__).parent / "library"
EXTENSION = ".pine"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}\Z")
PREFIX = "script:"
_COMPILED: dict[tuple[str, float], tuple[Script | None, ScriptError | None]] = {}


def scripts_dir() -> Path:
    """Directory of the user's scripts."""
    return default_home() / "scripts"


def check_name(name: str) -> str:
    """Validate a script name (lowercase letters, digits, ``-`` and ``_``)."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(
            "Script adı küçük harf, rakam, '-' ve '_' içerebilir"
            " (en fazla 48 karakter), ör. rsi_donusu."
        )
    return name


def _paths(name: str) -> tuple[Path, Path]:
    return scripts_dir() / f"{name}{EXTENSION}", LIBRARY / f"{name}{EXTENSION}"


def _locate(name: str) -> tuple[Path, bool]:
    check_name(name)
    user, builtin = _paths(name)
    if user.is_file():
        return user, False
    if builtin.is_file():
        return builtin, True
    raise KeyError(name)


def description(source: str) -> str:
    """Return the comment lines at the top of a script, without ``//@version``."""
    lines = []
    for line in source.splitlines():
        text = line.strip()
        if not text:
            if lines:
                break
            continue
        if not text.startswith("//"):
            break
        text = text[2:].strip()
        if text.startswith("@"):
            continue
        lines.append(text)
    return " ".join(lines)


def _compiled(path: Path, name: str) -> tuple[Script | None, ScriptError | None]:
    key = (str(path), path.stat().st_mtime)
    if key not in _COMPILED:
        try:
            _COMPILED[key] = (
                compile_script(path.read_text(encoding="utf-8"), name),
                None,
            )
        except ScriptError as error:
            _COMPILED[key] = (None, error)
    return _COMPILED[key]


def _entry(path: Path, builtin: bool) -> dict:
    name = path.stem
    source = path.read_text(encoding="utf-8")
    script, error = _compiled(path, name)
    entry = {
        "name": name,
        "builtin": builtin,
        "customized": not builtin and (LIBRARY / path.name).is_file(),
        "updated": datetime.fromtimestamp(path.stat().st_mtime).isoformat(
            timespec="seconds"
        ),
        "description": description(source),
        "error": error.to_dict() if error else None,
    }
    if script is not None:
        entry.update(script.describe())
    else:
        entry.update(kind=None, title=name, overlay=False, inputs=[])
    entry["name"] = name
    return entry


def list_scripts() -> list[dict]:
    """List library and user scripts; user copies replace library ones."""
    entries = {path.stem: _entry(path, True) for path in LIBRARY.glob(f"*{EXTENSION}")}
    folder = scripts_dir()
    if folder.is_dir():
        for path in folder.glob(f"*{EXTENSION}"):
            if NAME_RE.match(path.stem):
                entries[path.stem] = _entry(path, False)
    return sorted(
        entries.values(),
        key=lambda e: (e["kind"] != "strategy", e["builtin"], e["title"].lower()),
    )


def get_script(name: str) -> dict:
    """Return a script's source and whether it comes from the library."""
    path, builtin = _locate(name)
    return {**_entry(path, builtin), "source": path.read_text(encoding="utf-8")}


def save_script(name: str, source: str) -> dict:
    """Save a script, even if it has errors, and report them."""
    check_name(name)
    if len(source) > MAX_SOURCE:
        raise ValueError(f"Script çok uzun (en fazla {MAX_SOURCE} karakter).")
    folder = scripts_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}{EXTENSION}"
    fd, temporary = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(source)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return get_script(name)


def delete_script(name: str) -> bool:
    """Delete a user script. Returns whether a library version remains."""
    user, builtin = _paths(check_name(name))
    if not user.is_file():
        if builtin.is_file():
            raise ValueError("Hazır kütüphane scriptleri silinemez.")
        raise KeyError(name)
    user.unlink()
    return builtin.is_file()


def load_script(name: str) -> Script:
    """Compile a stored script by name."""
    path, _ = _locate(name)
    script, error = _compiled(path, name)
    if error is not None:
        raise error
    return script


def load_strategy(name: str) -> type[ScriptStrategy]:
    """Return the strategy class of a stored strategy script."""
    return script_strategy(load_script(name))


def script_strategies() -> dict[str, type[ScriptStrategy]]:
    """Return every error-free strategy script as ``{"script:name": class}``."""
    strategies = {}
    for entry in list_scripts():
        if entry["kind"] == "strategy" and not entry["error"]:
            strategies[PREFIX + entry["name"]] = load_strategy(entry["name"])
    return strategies
