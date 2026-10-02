"""What the decision model learns: the strategy it serves and its own results.

Before a blind test the decision model gets a short summary of the strategy
script and the strategy's track record on the bars before the test, so it
judges each signal by the strategy's own logic instead of rejecting, say, a
dip-buying signal for its falling momentum.

During the test a journal keeps every decision with its outcome: the trade's
return for a buy, the signal's trade without the AI for a rejected entry
(what was missed or avoided) and the move over the next bars for a sell or a
hold. An outcome is shown only to decisions made on or after the bar where
it became known, with letters instead of tickers and no dates.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from marketalyzer.ai.decide import TIMEOUT
from marketalyzer.ai.openrouter import OpenRouterError
from marketalyzer.services import number

_DATE = re.compile(r"\b(19|20)\d\d-\d\d-\d\d\b")
TYPES = ("dönüş", "trend", "kırılım", "karma")
MAX_BRIEF = 300
# Bars after the next open over which a sell, a hold or an untraded entry is judged.
HORIZON = 10
# Recent outcomes listed in each view.
RECENT = 6

BRIEF_SYSTEM = """Bir Pine Script strateji kodunu, bu stratejinin sinyallerini tek tek
onaylayacak hızlı bir karar modeline anlatacaksın. Yalnızca tek satır JSON yaz:
{"tur": "dönüş|trend|kırılım|karma", "ozet": "en fazla 2 cümle: hangi koşulda alır,
hangi koşulda satar, hangi piyasa durumundan kazanç bekler"}"""
UNKNOWN = {"tur": "bilinmiyor", "ozet": ""}


def track_record(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize trades for the model: count, share of winners, averages."""
    if not trades:
        return {"islem": 0}
    returns = [trade.get("return_pct") or 0.0 for trade in trades]
    return {
        "islem": len(trades),
        "kazancli_%": number(sum(r > 0 for r in returns) / len(returns) * 100, 1),
        "ort_getiri_%": number(sum(returns) / len(returns), 2),
        "ort_bar": number(sum(t.get("bars") or 0 for t in trades) / len(trades), 1),
    }


def _parse_brief(text: str) -> dict[str, str]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return dict(UNKNOWN)
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return dict(UNKNOWN)
    if not isinstance(data, dict):
        return dict(UNKNOWN)
    kind = str(data.get("tur") or "").strip().lower()
    summary = " ".join(str(data.get("ozet") or "").split())
    summary = _DATE.sub("", summary)[:MAX_BRIEF].strip()
    if kind not in TYPES or not summary:
        return dict(UNKNOWN)
    return {"tur": kind, "ozet": summary}


def strategy_brief(decider: Any, source: str) -> dict[str, str]:
    """Ask the decision model, once per script, what the strategy does.

    The answer is cached with the decisions, keyed by the model and the
    source. Any failure gives ``{"tur": "bilinmiyor", "ozet": ""}``: the test
    goes on without the summary.
    """
    key = hashlib.sha256(
        f"brief\n{decider.model}\n{BRIEF_SYSTEM}\n{source}".encode()
    ).hexdigest()
    stored = decider.cache.get(key) if decider.cache else None
    if stored:
        return _parse_brief(stored["text"])
    messages = [
        {"role": "system", "content": BRIEF_SYSTEM},
        {"role": "user", "content": source},
    ]
    try:
        text = decider.complete(
            decider.api_key,
            decider.model,
            messages,
            temperature=0.0,
            max_tokens=200,
            timeout=TIMEOUT,
        )["text"]
    except OpenRouterError:
        return dict(UNKNOWN)
    brief = _parse_brief(text)
    if brief != UNKNOWN and decider.cache:
        decider.cache.put(key, {"text": text})
    return brief


# --- Journal ------------------------------------------------------------------------


def features(view: dict[str, Any]) -> dict[str, Any]:
    """Keep the readings of a view that lessons are likely to be about."""
    trend = view.get("trend") or {}
    momentum = view.get("momentum") or {}
    returns = view.get("getiri_%") or {}
    return {
        "rsi": momentum.get("rsi"),
        "supertrend": trend.get("supertrend"),
        "adx": trend.get("adx"),
        "getiri_5_bar_%": returns.get("5_bar"),
        "getiri_20_bar_%": returns.get("20_bar"),
        "bollinger_%b": (view.get("oynaklik") or {}).get("bollinger_%b"),
        "hacim_orani": (view.get("hacim") or {}).get("hacim_ort20_orani"),
        "sinyaller": [s["sinyal"] for s in view.get("aktif_sinyaller") or []][:4],
    }


@dataclass
class Entry:
    """One decision in the journal and, once known, its outcome."""

    order: int
    code: str
    letter: str
    event: str  # giriş, çıkış or gözden geçirme
    label: str  # AL, SAT, BEKLE or TUT
    action: str  # buy, sell or hold
    holding: bool  # whether a position was open when deciding
    confidence: int | None
    features: dict[str, Any]
    record: dict[str, Any] = field(repr=False)
    pct: float | None = None
    kind: str | None = None
    at: Any = None  # the bar where the outcome became known

    def verdict(self) -> str:
        """Say whether the decision proved right, in a few words."""
        gain = (self.pct or 0) > 0
        if self.label == "BEKLE":
            return "kaçırıldı" if gain else "doğru kaçınıldı"
        if self.label == "AL":
            return "doğru" if gain else "zarar"
        if self.label == "SAT":
            return "erken satış" if gain else "doğru satış"
        return "doğru" if (self.pct or 0) >= 0 else "tutmak zarar"

    def right(self) -> bool:
        """Return whether a decision on an open position proved right."""
        return (self.pct or 0) < 0 if self.action == "sell" else (self.pct or 0) >= 0

    def line(self) -> str:
        """Describe the decision and its outcome without names or dates."""
        confidence = f" %{self.confidence}" if self.confidence is not None else ""
        return (
            f"{self.letter} · {self.event} · {self.label}{confidence} → {self.kind}"
            f" %{self.pct:+.1f} ({self.verdict()})"
        )


def _share(entries: list[Entry], name: str) -> dict[str, Any]:
    returns = [e.pct or 0.0 for e in entries]
    return {
        "adet": len(entries),
        name: number(sum(r > 0 for r in returns) / len(returns) * 100, 1),
        "ort_%": number(sum(returns) / len(returns), 2),
    }


class Journal:
    """The decisions of a blind test and the outcomes known so far.

    ``letters`` maps each ticker to its letter. Outcomes are attached with
    the bar where they became known; ``view(now)`` shows only those known by
    ``now``. ``lessons`` are written from the journal by ``reflect``.
    """

    def __init__(self, letters: dict[str, str]):
        self.letters = letters
        self.entries: list[Entry] = []
        self.lessons: list[str] = []
        self.history: list[dict[str, Any]] = []
        self._marked = 0

    def add(
        self, record: dict[str, Any], *, holding: bool, features: dict[str, Any]
    ) -> Entry:
        """Keep a decision; its outcome comes later through ``resolve``."""
        entry = Entry(
            order=len(self.entries),
            code=record["symbol"],
            letter=self.letters.get(record["symbol"], "Hisse ?"),
            event=record["event"],
            label=record["label"],
            action=record["action"],
            holding=holding,
            confidence=record.get("confidence"),
            features=features,
            record=record,
        )
        self.entries.append(entry)
        return entry

    def resolve(self, entry: Entry, pct: float, kind: str, at: Any) -> None:
        """Attach the outcome, known from bar ``at`` on, to the decision."""
        entry.pct, entry.kind, entry.at = number(pct, 2), kind, at
        entry.record["outcome_pct"] = entry.pct
        entry.record["outcome_kind"] = kind

    def known(self, now: Any) -> list[Entry]:
        """Return the decisions whose outcome is known on bar ``now``."""
        return [e for e in self.entries if e.at is not None and e.at <= now]

    def fresh(self, now: Any) -> int:
        """Count the outcomes known by ``now`` since the last ``mark``."""
        return len(self.known(now)) - self._marked

    def mark(self, now: Any) -> None:
        """Note that the outcomes known by ``now`` have been reflected on."""
        self._marked = len(self.known(now))

    def view(self, now: Any) -> dict[str, Any] | None:
        """Summarize what is known on bar ``now`` for a decision's view."""
        done = self.known(now)
        if not done and not self.lessons:
            return None
        entries = [e for e in done if not e.holding]
        taken = [e for e in entries if e.action == "buy"]
        passed = [e for e in entries if e.action == "hold"]
        positions = [e for e in done if e.holding]
        summary: dict[str, Any] = {}
        if taken:
            summary["alinan_giris"] = _share(taken, "kazancli_%")
        if passed:
            summary["reddedilen_giris"] = _share(passed, "kar_ettirecek_%")
        if positions:
            right = sum(e.right() for e in positions)
            summary["pozisyon_kararlari"] = {
                "adet": len(positions),
                "dogru_%": number(right / len(positions) * 100, 1),
            }
        recent = sorted(done, key=lambda e: (e.at, e.order))[-RECENT:]
        view: dict[str, Any] = {
            "ozet": summary,
            "son_sonuclar": [e.line() for e in recent],
        }
        if self.lessons:
            view["dersler"] = list(self.lessons)
        return view
