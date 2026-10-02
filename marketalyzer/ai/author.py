"""Write a strategy script from a signal study, check it and save it.

The model gets the study of the training period only, with letters instead of
tickers and no dates, so the strategy cannot encode what happened after the
cutoff. Its script is compiled, dry-run and run on every symbol's training
bars; problems go back to the model, up to ``MAX_ATTEMPTS`` times.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer import research, services
from marketalyzer.ai.openrouter import stream_chat
from marketalyzer.ai.prompt import LANGUAGE, _function_list
from marketalyzer.paper.models import IST
from marketalyzer.scripting import ScriptError, compile_script, save_script

MAX_ATTEMPTS = 3
MAX_TOKENS = 6000
TEMPERATURE = 0.2
MAX_GOAL = 1000
# More entries than one every this many bars is churning, not a strategy.
MIN_BARS_PER_ENTRY = 3

ROLE = """Sen Borsa İstanbul için kural tabanlı strateji yazan deneyimli bir quant
geliştiricisin. Sana seçilen hisselerin eğitim dönemindeki sinyal araştırması
verilir. Hisse adları ve tarihler gizlidir: piyasa hakkındaki bilgini değil, yalnızca
verilen istatistikleri kullan.

Görevin bu hisselerin hepsinde kullanılabilecek tek bir strateji scripti yazmak:
- //@version=5 ve strategy("Ad", overlay=true) ile başla. Yalnızca uzun pozisyon.
- En fazla 5 input kullan; her input'a minval, maxval ve step ver (optimizasyon ve
  walk-forward için).
- Kuralları araştırmadaki bulgulara dayandır: birden çok hissede desteklenen, "güçlü"
  ya da tutarlı "zayıf" sinyalleri kullan; "az örnek" ve "etkisiz" sinyallere dayanma.
  Beklenenin tersine çalışmış bir sinyali ya kullanma ya da gözlenen yönde kullan.
- Hisselerin eğilimine uy: momentum eğilimliyse trend/kırılım, ortalamaya dönüş
  eğilimliyse geri çekilme/dönüş kuralları; oynaklığa göre ATR tabanlı stop.
- Mutlaka bir çıkış kuralı (strategy.close) ve strategy.exit ile zarar durdur içersin.
- Giriş ve çıkış koşullarını plotshape ile işaretle. Bu sinyalleri daha sonra hızlı bir
  yapay zeka karar katmanı tek tek onaylayacak ya da reddedecek; bu yüzden sinyaller
  seçici olsun, her barda tetiklenmesin.
- Aşırı uyumdan kaçın: az sayıda basit kural, yuvarlak eşikler.
- Kod içindeki açıklamalar Türkçe olsun.

Yanıt biçimi: önce tek bir ```pine bloğunda kodun tamamı, sonra en fazla 6 madde ile
hangi bulguya dayandığını kısaca açıkla."""


class Cancelled(Exception):
    """The person stopped the job."""


def system_prompt() -> str:
    """Return the author's instructions with the script language reference."""
    return "\n\n".join([ROLE, LANGUAGE, _function_list()])


def user_prompt(summary: str, goal: str | None, count: int) -> str:
    """Return the request with the anonymous study."""
    goal = (goal or "").strip()[:MAX_GOAL] or (
        "Dengeli risk ve getiri; az ama kaliteli işlem."
    )
    return (
        f"Hedef: {goal}\nHisse sayısı: {count}\n\n"
        f"Sinyal araştırması (yalnızca eğitim dönemi):\n{summary}"
    )


def extract_code(text: str) -> str | None:
    """Return the script in a reply: the ```pine block, else any code block."""
    for pattern in (r"```pine[^\n]*\n(.*?)```", r"```[a-z]*\n(.*?)```"):
        match = re.search(pattern, text or "", re.S)
        if match and match.group(1).strip():
            return match.group(1).strip() + "\n"
    stripped = (text or "").strip()
    if stripped.startswith("//@version"):
        return stripped + "\n"
    return None


def explanation(text: str) -> str:
    """Return what the model wrote after its code block."""
    parts = re.split(r"```[^\n]*\n.*?```", text or "", flags=re.S)
    return parts[-1].strip() if len(parts) > 1 else ""


def default_name(codes: list[str], when: datetime | None = None) -> str:
    """Name a generated script after its symbols and the time it was written."""
    when = when or datetime.now(IST)
    stem = "_".join(code.lower() for code in codes)[:24].rstrip("_")
    return f"ai_{stem}_{when:%Y%m%d_%H%M}"


def _entries(script, frames: dict[str, tuple[pd.DataFrame, Any]]) -> dict[str, Any]:
    """Count each symbol's entries in its training window."""
    counts = {}
    for code, (frame, shown) in frames.items():
        result = script.run(frame, symbol=code)
        first = (
            int(np.searchsorted(pd.DatetimeIndex(frame.index), shown))
            if shown is not None
            else 0
        )
        entries, _ = services._transitions(result.entries, result.exits)
        counts[code] = {
            "entries": int(entries[first:].sum()) if entries is not None else 0,
            "bars": len(frame) - first,
        }
    return counts


def review(source: str, frames: dict[str, tuple[pd.DataFrame, Any]]) -> dict[str, Any]:
    """Check a generated script; ``problem`` says what the model must fix."""
    check = services.check_script(source)
    if not check["ok"]:
        error = check["error"]
        where = f"Satır {error['line']}: " if error.get("line") else ""
        return {"check": check, "problem": f"Derleme hatası. {where}{error['message']}"}
    if check.get("kind") != "strategy":
        return {
            "check": check,
            "problem": "Script strategy() ile bildirilmeli ve strategy.entry ile"
            " giriş yapmalı.",
        }
    try:
        counts = _entries(compile_script(source, "ai"), frames)
    except ScriptError as error:
        return {"check": check, "problem": f"Eğitim verisinde hata: {error.located()}"}
    total = sum(item["entries"] for item in counts.values())
    bars = sum(item["bars"] for item in counts.values())
    problem = None
    if total == 0:
        problem = (
            "Eğitim döneminde hiçbir hissede giriş sinyali üretmedi. Koşulları"
            " gevşet ya da araştırmada sık görülen sinyalleri kullan."
        )
    elif bars / total < MIN_BARS_PER_ENTRY:
        problem = (
            f"Çok sık giriş üretiyor ({total} giriş / {bars} bar). Girişi bir olay"
            " (kesişim, kırılım) ile sınırla ve filtre ekle."
        )
    return {"check": check, "problem": problem, "entries": counts}


def _stream_reply(
    stream: Callable[..., Any],
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    emit: Callable[[dict[str, Any]], None],
    cancelled: Callable[[], bool],
    usage: dict[str, float],
) -> str:
    reply = []
    events = stream(
        api_key, model, messages, temperature=TEMPERATURE, max_tokens=MAX_TOKENS
    )
    try:
        for event in events:
            if cancelled():
                raise Cancelled
            if event["type"] == "text":
                reply.append(event["text"])
                emit({"type": "text", "text": event["text"]})
            elif event["type"] == "usage":
                for key in ("prompt_tokens", "completion_tokens", "cost"):
                    usage[key] = usage.get(key, 0) + event[key]
    finally:
        close = getattr(events, "close", None)
        if close:
            close()
    return "".join(reply)


def author_script(
    symbols: list[str],
    *,
    cutoff: str | None,
    years: float,
    api_key: str,
    model: str,
    emit: Callable[[dict[str, Any]], None],
    cancelled: Callable[[], bool] = lambda: False,
    goal: str | None = None,
    name: str | None = None,
    interval: str = "1d",
    stream: Callable[..., Any] = stream_chat,
) -> dict[str, Any]:
    """Study the symbols, have the model write a strategy, check it and save it.

    Emits ``stage``, ``study``, ``text`` (the reply as it is written),
    ``review`` and finally ``result`` events.
    """
    emit({"type": "stage", "stage": "study", "message": "Sinyaller inceleniyor"})
    result, frames, _ = research.prepare(symbols, cutoff, years, interval)
    codes = [item["symbol"] for item in result["symbols"]]
    emit({"type": "study", "study": result})
    anon = research.anonymize(result)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt()},
        {
            "role": "user",
            "content": user_prompt(research.summary_text(anon), goal, len(codes)),
        },
    ]
    usage: dict[str, float] = {}
    source = None
    reply = ""
    checked: dict[str, Any] = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        emit(
            {
                "type": "stage",
                "stage": "writing" if attempt == 1 else "fixing",
                "attempt": attempt,
                "message": "Model scripti yazıyor"
                if attempt == 1
                else f"Model scripti düzeltiyor ({attempt}. deneme)",
            }
        )
        reply = _stream_reply(stream, api_key, model, messages, emit, cancelled, usage)
        code = extract_code(reply)
        emit({"type": "stage", "stage": "checking", "message": "Script doğrulanıyor"})
        if code is None:
            checked = {"check": None, "problem": "Yanıtta ```pine kod bloğu yok."}
        else:
            checked = review(code, frames)
        emit({"type": "review", "attempt": attempt, **checked})
        if code is not None and not checked["problem"]:
            source = code
            break
        messages += [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": f"Scriptte sorun var: {checked['problem']}\nDüzeltip kodun"
                " tamamını tek bir ```pine bloğunda yeniden ver.",
            },
        ]
    if source is None:
        raise ValueError(
            f"Model {MAX_ATTEMPTS} denemede çalışan bir strateji yazamadı:"
            f" {checked.get('problem')}"
        )
    name = name or default_name(codes)
    saved = save_script(name, source)
    emit({"type": "stage", "stage": "saved", "message": f"Kaydedildi: {name}"})
    return {
        "name": name,
        "source": source,
        "script": saved,
        "explanation": explanation(reply),
        "attempts": attempt,
        "entries": checked.get("entries"),
        "model": model,
        "usage": usage,
        "symbols": codes,
        "cutoff": result["cutoff"],
        "train_start": result["start"],
    }
