"""What the decision model learns: the strategy it serves and its own results.

Before a blind test the decision model gets a short summary of the strategy
script and the strategy's track record on the bars before the test, so it
judges each signal by the strategy's own logic instead of rejecting, say, a
dip-buying signal for its falling momentum.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from marketalyzer.ai.decide import TIMEOUT
from marketalyzer.ai.openrouter import OpenRouterError
from marketalyzer.services import number

_DATE = re.compile(r"\b(19|20)\d\d-\d\d-\d\d\b")
TYPES = ("dönüş", "trend", "kırılım", "karma")
MAX_BRIEF = 300

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
