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

With the strategy's past signals at hand (``evidence.Pool`` for entries and
for exits), the coach writes rules over the signal readings instead of free
lessons, and a rule is kept only if the past signals it picks did what it
claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from marketalyzer.ai import evidence
from marketalyzer.ai.openrouter import OpenRouterError, complete_chat, stream_chat
from marketalyzer.services import number

_DATE = re.compile(r"\b(19|20)\d\d-\d\d-\d\d\b")
TYPES = ("dönüş", "trend", "kırılım", "karma")
MAX_BRIEF = 400
BRIEF_TOKENS = 800
# Bars after the next open over which a sell, a hold or an untraded entry is judged:
# two weeks of daily bars; about five trading days of hourly bars.
HORIZON = 10
HORIZONS = {"1h": 45}


def horizon(interval: str) -> int:
    """Return the bars over which outcomes are judged on ``interval``."""
    return HORIZONS.get(interval, HORIZON)


# Recent outcomes listed in each view.
RECENT = 6
# Lessons are rewritten once this many new outcomes are known.
REFLECT_EVERY = 6
# Rules rest on many past signals, so they are rewritten less often.
RULES_EVERY = 12
# Rules that failed the check, kept to show the coach what did not hold.
MAX_REJECTED = 12
MAX_LESSONS = 5
MAX_LESSON = 300
# The most recent outcomes the coach reads.
REFLECT_ITEMS = 40
REFLECT_TIMEOUT = (10.0, 90.0)
REFLECT_TOKENS = 3000

REFLECT_SYSTEM = """Sen Borsa İstanbul kör testinde strateji sinyallerini onaylayan ya da
reddeden hızlı bir karar modelinin koçusun. Sana stratejinin özeti ve test öncesi
sonucu, mevcut ders notları ve modelin sonucu belli olmuş kararları verilir; hisseler
harfle anılır, tarih yoktur.

Görevin, modelin sonuçlarından en fazla 5 kısa ve uygulanabilir ders çıkarmak:
- Hangi durumlarda sinyali uygulamak kazandırdı, hangilerinde reddetmek doğruydu?
  Kaçırılan kârlı sinyaller de hatadır: amaç getiriyi ve işlem sayısını artırmak,
  gereksiz reddetmeyi azaltmaktır.
- Somut göstergeler ve eşikler kullan (ör. "RSI 30'un altında ve hacim ortalamanın
  1,5 katıyken giriş sinyalini uygula").
- Az örneğe dayanan genellemelerden kaçın; mevcut dersleri sonuçlar destekliyorsa
  koru, çürütüyorsa değiştir.
- Her ders tek cümle ve en fazla 40 kelime olsun; açıklama ya da örnek listesi yazma.
- Hisse adı ya da tarih yazma.
Yalnızca JSON yaz: {"dersler": ["...", "..."]}"""

RULES_SYSTEM = """Sen Borsa İstanbul kör testinde strateji sinyallerini onaylayan ya da
reddeden hızlı bir karar modelinin koçusun. Sana stratejinin özeti, geçmiş giriş
sinyallerinin sonucu ("tum_gecmis_sinyaller", maliyet dahil işlem getirisi), geçmiş
çıkış sinyallerinden sonra birkaç bar daha tutmanın sonucu ("tum_gecmis_cikislar"),
her ölçümün üçte birlik dilimlerindeki sonuçlar ("olcum_dilimleri",
"cikis_olcum_dilimleri"), mevcut kurallar, geçmişte tutmayan kurallar ve modelin
sonucu belli olmuş son kararları (o andaki ölçümleriyle) verilir; hisseler harfle
anılır, tarih yoktur.

Görevin en fazla 5 kural önermek. Giriş kuralının eylemi "reddet" ya da "uygula",
çıkış kuralınınki "tut" (çıkış sinyaline rağmen pozisyonu koru) ya da "sat"tır.
- Her kural 1-3 koşuldan oluşur; yalnızca "olcumler" içindeki adları ve <, <=, >, >=
  işlemlerini kullan. Eşikleri dilimlerdeki aralıklardan seç.
- Her kural kendi türündeki geçmiş tüm sinyallerde sınanır: en az 10 sinyal
  seçmeyen ya da seçtiği sinyallerin ortalaması iddiasını (reddet/sat: negatif ve
  genelden belirgin kötü; uygula/tut: pozitif ve genelden belirgin iyi)
  tutturmayan kural atılır. Bu yüzden az koşullu, çok sinyale dayanan kurallar
  yaz; tutmayan kuralları tekrarlama.
- Mevcut kurallar hâlâ geçerliyse koru.
Yalnızca JSON yaz: {"kurallar": [{"kosullar": [["rsi", "<", 45]], "eylem": "reddet"}]}"""


@dataclass
class Coach:
    """The model that writes lessons and revises scripts, with its key."""

    api_key: str
    model: str
    complete: Callable[..., dict[str, Any]] = complete_chat
    stream: Callable[..., Any] = stream_chat


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
    said = str(data.get("tur") or "").strip().lower()
    # "ortalamaya dönüş" is a dönüş strategy; a mix of words is karma.
    named = [kind for kind in TYPES if kind in said]
    kind = named[0] if len(named) == 1 else ("karma" if named or said else "")
    summary = " ".join(str(data.get("ozet") or "").split())
    summary = _DATE.sub("", summary)[:MAX_BRIEF].strip()
    if not kind or not summary:
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
        # Reasoning models spend tokens before answering: leave them room.
        text = decider.ask(messages, max_tokens=BRIEF_TOKENS)["text"]
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
        # Verified rules with their record; their texts are also the lessons.
        self.rules: list[tuple[evidence.Rule, dict[str, Any]]] = []
        self.rejected: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.cost = 0.0  # what writing the lessons cost, in USD
        self.reflections = 0  # coach calls
        self.unusable = 0  # calls that failed or gave no usable lesson
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
        if self.rules:
            view["kurallar"] = [evidence.describe(row) for _, row in self.rules]
        elif self.lessons:
            view["dersler"] = list(self.lessons)
        return view

    def matching(self, values: dict[str, Any], exit: bool = False) -> list[str]:
        """Return the verified entry (or exit) rules that hold for the readings."""
        return [
            evidence.describe(row)
            for rule, row in self.rules
            if rule.exit == exit and rule.matches(values)
        ]


# --- Lessons ------------------------------------------------------------------------


_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def parse_lessons(text: str) -> list[str]:
    """Read the lessons from ``{"dersler": [...]}`` or from a bulleted list.

    A reply cut off by the token limit keeps the lessons it finished.
    """
    text = text or ""
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("dersler"), list):
            return [str(item) for item in data["dersler"]]
    start = re.search(r'"dersler"\s*:\s*\[', text)
    if start:
        return [json.loads(item) for item in _STRING.findall(text[start.end() :])]
    bullet = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+(.*\S)")
    return [m.group(1) for line in text.splitlines() if (m := bullet.match(line))]


def clean_lessons(items: list[str], codes: list[str]) -> list[str]:
    """Keep at most ``MAX_LESSONS`` short lessons that name no stock or date."""
    tickers = (
        re.compile(r"\b(?:" + "|".join(re.escape(code) for code in codes) + r")\b")
        if codes
        else None
    )
    lessons = []
    for item in items:
        text = " ".join(str(item).split()).strip(" -•*")
        if not text or _DATE.search(text) or (tickers and tickers.search(text)):
            continue
        if len(text) > MAX_LESSON:  # Shorten at a word, keep the lesson.
            cut = text[:MAX_LESSON]
            cut = cut[: cut.rfind(" ")] if " " in cut else cut
            text = cut.rstrip(" ,;:") + "…"
        lessons.append(text)
        if len(lessons) == MAX_LESSONS:
            break
    return lessons


def reflect(
    journal: Journal,
    now: Any,
    coach: Coach,
    *,
    strategy: dict[str, Any] | None,
    codes: list[str],
) -> list[str] | None:
    """Have the coach rewrite the lessons from the outcomes known by ``now``.

    Returns None when the coach fails or writes nothing usable; the old
    lessons then stay.
    """
    done = sorted(journal.known(now), key=lambda e: (e.at, e.order))[-REFLECT_ITEMS:]
    payload = {
        "strateji": strategy or {},
        "mevcut_dersler": journal.lessons,
        "kararlar": [
            {
                "hisse": e.letter,
                "olay": e.event,
                "karar": e.label,
                "guven": e.confidence,
                "sonuc_%": e.pct,
                "sonuc_turu": e.kind,
                "degerlendirme": e.verdict(),
                "ozellikler": e.features,
            }
            for e in done
        ],
    }
    messages = [
        {"role": "system", "content": REFLECT_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    journal.reflections += 1
    try:
        answer = coach.complete(
            coach.api_key,
            coach.model,
            messages,
            temperature=0.2,
            max_tokens=REFLECT_TOKENS,
            timeout=REFLECT_TIMEOUT,
        )
    except OpenRouterError:
        journal.unusable += 1
        return None
    journal.cost += float((answer.get("usage") or {}).get("cost") or 0.0)
    lessons = clean_lessons(parse_lessons(answer["text"]), codes)
    if not lessons:
        journal.unusable += 1
    return lessons or None


def reflect_rules(
    journal: Journal,
    now: Any,
    coach: Coach,
    pool: evidence.Pool,
    *,
    exits: evidence.Pool | None = None,
    strategy: dict[str, Any] | None,
) -> list[str]:
    """Have the coach propose rules, keep those the past signals bear out.

    Entry rules are checked on ``pool``, exit rules on ``exits``. The current
    rules are checked again on everything known by ``now``, so a rule that
    stops holding is dropped. Returns the kept rules' texts (also the
    journal's lessons); the coach failing leaves only the re-check.
    """
    known = pool.known(now)
    after = exits.known(now) if exits is not None else []
    done = sorted(journal.known(now), key=lambda e: (e.at, e.order))[-REFLECT_ITEMS:]
    payload = {
        "strateji": strategy or {},
        "olcumler": evidence.FEATURES,
        "tum_gecmis_sinyaller": evidence.stats(known),
        "olcum_dilimleri": evidence.slices(known),
        "tum_gecmis_cikislar": evidence.stats(after),
        "cikis_olcum_dilimleri": evidence.slices(after),
        "mevcut_kurallar": [row for _, row in journal.rules],
        "tutmayan_kurallar": journal.rejected[-MAX_REJECTED:],
        "son_kararlar": [
            {
                "hisse": e.letter,
                "olay": e.event,
                "karar": e.label,
                "sonuc_%": e.pct,
                "degerlendirme": e.verdict(),
                "olcumler": e.features,
            }
            for e in done
        ],
    }
    messages = [
        {"role": "system", "content": RULES_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    journal.reflections += 1
    proposed: list[evidence.Rule] = []
    try:
        answer = coach.complete(
            coach.api_key,
            coach.model,
            messages,
            temperature=0.2,
            max_tokens=REFLECT_TOKENS,
            timeout=REFLECT_TIMEOUT,
        )
        journal.cost += float((answer.get("usage") or {}).get("cost") or 0.0)
        proposed = evidence.parse_rules(answer["text"])
    except OpenRouterError:
        pass
    if not proposed:
        journal.unusable += 1
    candidates = [rule for rule, _ in journal.rules]
    candidates += [rule for rule in proposed if rule not in candidates]
    kept, failed = [], []
    for rule in candidates:
        row = evidence.verify(rule, after if rule.exit else known)
        (kept if row["kabul"] else failed).append((rule, row))
    kept.sort(key=lambda item: -abs(item[1].get("t") or 0))
    journal.rules = kept[: evidence.MAX_RULES]
    journal.rejected = (journal.rejected + [row for _, row in failed])[-MAX_REJECTED:]
    journal.lessons = [evidence.describe(row) for _, row in journal.rules]
    return journal.lessons
