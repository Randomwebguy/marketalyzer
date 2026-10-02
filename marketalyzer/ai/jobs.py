"""Run long AI jobs (script writing, blind tests) in threads and stream events.

A job is a function ``work(emit, cancelled)`` that emits progress events and
returns its result. The runner adds ``result``, ``error`` or ``stopped`` and
always ends with ``done``, like a chat turn.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from marketalyzer.ai.author import Cancelled as AuthorCancelled
from marketalyzer.ai.openrouter import OpenRouterError
from marketalyzer.blind import Cancelled as BlindCancelled
from marketalyzer.scripting import ScriptError

MAX_RUNNING = 3

Emit = Callable[[dict[str, Any]], None]
Work = Callable[[Emit, Callable[[], bool]], dict[str, Any]]


class JobError(Exception):
    """A job that cannot start, with an HTTP status for the API."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class JobRunner:
    """Start, stream and stop background jobs."""

    def __init__(self, limit: int = MAX_RUNNING):
        self.limit = limit
        self._running: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def running(self) -> list[str]:
        """Return the ids of the running jobs."""
        with self._lock:
            return list(self._running)

    def stop(self, job_id: str) -> bool:
        """Ask a job to stop; return whether it was running."""
        event = self._running.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    def start(self, kind: str, work: Work, emit: Emit) -> tuple[str, threading.Event]:
        """Run ``work`` in a thread; raise JobError when too many jobs run."""
        job_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        with self._lock:
            if len(self._running) >= self.limit:
                raise JobError(
                    "Aynı anda çok fazla iş çalışıyor; birinin bitmesini bekleyin.", 429
                )
            self._running[job_id] = cancel
        thread = threading.Thread(
            target=self._run,
            args=(job_id, kind, work, emit, cancel),
            daemon=True,
            name=f"{kind}-{job_id}",
        )
        thread.start()
        return job_id, cancel

    def _run(self, job_id, kind, work, emit, cancel) -> None:
        try:
            result = work(emit, cancel.is_set)
            emit({"type": "result", "kind": kind, "result": result})
        except (AuthorCancelled, BlindCancelled):
            emit({"type": "stopped"})
        except OpenRouterError as error:
            emit({"type": "error", "message": error.message})
        except ScriptError as error:
            emit({"type": "error", "message": error.located()})
        except (ValueError, KeyError) as error:
            emit({"type": "error", "message": str(error).strip("'\"")})
        except Exception as error:  # Report anything else instead of hanging the UI.
            emit({"type": "error", "message": f"Beklenmeyen hata: {error}"})
        finally:
            with self._lock:
                self._running.pop(job_id, None)
            emit({"type": "done", "job_id": job_id})
