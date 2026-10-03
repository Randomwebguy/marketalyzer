"""Blind test runs kept on disk, so a result can be read again later.

Every AI blind test is saved as ``<home>/ai/lab/runs/<id>.json`` (readable
only by the owner). The newest run that learned with a script also gives
that script's lessons to live decisions.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from marketalyzer.ai.settings import ai_home, write_json
from marketalyzer.paper.models import IST

RUN_ID = re.compile(r"^\d{8}_\d{6}_\d{6}$")


def runs_dir() -> Path:
    """Return the directory holding the saved runs."""
    return ai_home() / "lab" / "runs"


def save(result: dict[str, Any]) -> str:
    """Save a run's result and return its id (time-ordered)."""
    now = datetime.now(IST)
    run_id = f"{now:%Y%m%d_%H%M%S_%f}"
    while (runs_dir() / f"{run_id}.json").exists():
        now = now.replace(microsecond=(now.microsecond + 1) % 1_000_000)
        run_id = f"{now:%Y%m%d_%H%M%S_%f}"
    write_json(
        runs_dir() / f"{run_id}.json",
        {**result, "run_id": run_id, "saved": now.isoformat(timespec="seconds")},
    )
    return run_id


def load(run_id: str) -> dict[str, Any]:
    """Return a saved run.

    Raises
    ------
    ValueError
        For an invalid id or a run that does not exist (Turkish message).
    """
    if not RUN_ID.match(run_id or ""):
        raise ValueError("Geçersiz koşu kimliği.")
    try:
        return json.loads((runs_dir() / f"{run_id}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Koşu bulunamadı.") from None


def _saved(limit: int | None = None) -> list[dict[str, Any]]:
    """Return the saved runs, newest first; unreadable files are skipped."""
    try:
        paths = sorted(runs_dir().glob("*.json"), reverse=True)
    except OSError:
        return []
    found = []
    for path in paths:
        if not RUN_ID.match(path.stem):
            continue
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if limit and len(found) >= limit:
            break
    return found


def scripts_of(run: dict[str, Any]) -> list[str]:
    """Return the scripts a run traded: the first one, then each revision."""
    names = [(run.get("config") or {}).get("script")]
    names += [row.get("script") for row in run.get("rounds") or []]
    return list(dict.fromkeys(name for name in names if name))


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Return a summary of the newest runs."""
    rows = []
    for run in _saved(limit):
        ai = run.get("ai") or {}
        rows.append(
            {
                "id": run.get("run_id"),
                "saved": run.get("saved"),
                "symbols": (run.get("config") or {}).get("symbols"),
                "scripts": scripts_of(run),
                "mode": (run.get("config") or {}).get("mode", "ai"),
                "period": run.get("period"),
                "learning": (run.get("learning") or {}).get("mode", "off"),
                "ai_return_pct": ai.get("return_pct"),
                "ai_trades": ai.get("trades"),
                "signals_return_pct": (run.get("signals") or {}).get("return_pct"),
                "hold_return_pct": (run.get("hold") or {}).get("return_pct"),
                "benchmark_return_pct": (run.get("benchmark") or {}).get("return_pct"),
            }
        )
    return rows


def latest_learning(script: str) -> dict[str, Any] | None:
    """Return the newest lessons learned while trading ``script``, if any."""
    for run in _saved():
        lessons = (run.get("learning") or {}).get("lessons")
        if lessons and script in scripts_of(run):
            return {"run_id": run.get("run_id"), "lessons": list(lessons)}
    return None
