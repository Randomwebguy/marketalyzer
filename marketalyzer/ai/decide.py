"""Fast buy/sell decisions by a language model, from an anonymous snapshot.

The model sees one bar of one stock at a time: indicator readings, the signals
firing on that bar (with how they did in the training period) and the open
position. The ticker, the dates and the price level are hidden, so it cannot
use what it may remember about how that stock later moved; it can only weigh
the numbers in front of it.

Decisions need to be quick, so the default model is a small, fast one, the
prompt is short, the answer is a tiny JSON object and OpenRouter is asked to
route to its lowest-latency provider. Answers are cached on disk: re-running
the same backtest gives the same decisions without new requests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer.ai.openrouter import (
    OpenRouterError,
    _version,
    account_blocked,
    complete_chat,
    list_models,
)
from marketalyzer.ai.settings import ai_home, private_dir
from marketalyzer.research import (
    BARS_PER_YEAR,
    SIGNAL_BY_KEY,
    active_signals,
    indicators,
)
from marketalyzer.services import number

MAX_TOKENS = 300
TEMPERATURE = 0.0
TIMEOUT = (10.0, 45.0)
RECENT_BARS = 20
MAX_PLOTS = 6
INTERVAL_NAMES = {"1d": "1 gün", "1W": "1 hafta", "1h": "1 saat"}
# Round-trip trading cost the model should beat: commission, BSMV and slippage.
ROUND_TRIP_COST_PCT = 0.5
DEFAULT_PRESET = "claude-haiku"


@dataclass(frozen=True)
class Preset:
    """A family of fast models; the newest available version is used."""

    key: str
    label: str
    note: str
    patterns: tuple[str, ...]
    fallback: str
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        """Return the preset for the UI."""
        return {"key": self.key, "label": self.label, "note": self.note}


PRESETS: tuple[Preset, ...] = (
    Preset(
        "claude-haiku",
        "Claude Haiku (Anthropic)",
        "Varsayılan. Hızlı yanıt verir, kısa JSON biçimine sadık kalır ve aynı"
        " veriye tutarlı karar verir.",
        (r"^anthropic/claude-haiku-[\d.]+$", r"^anthropic/claude-[\d.]+-haiku$"),
        "anthropic/claude-haiku-4.5",
    ),
    Preset(
        "gemini-flash",
        "Gemini Flash (Google)",
        "Hızlı ve güçlü; akıl yürütme düşük tutulur.",
        (r"^google/gemini-[\d.]+-flash$", r"^google/gemini-[\d.]+-flash-preview$"),
        "google/gemini-3.8-flash",
        {"reasoning": {"effort": "low"}},
    ),
    Preset(
        "gemini-flash-lite",
        "Gemini Flash Lite (Google)",
        "Çok düşük gecikme ve maliyet; gerekçeler daha yüzeysel olabilir.",
        (
            r"^google/gemini-[\d.]+-flash-lite$",
            r"^google/gemini-[\d.]+-flash-lite-preview$",
        ),
        "google/gemini-3.5-flash-lite",
    ),
    Preset(
        "gpt-mini",
        "GPT mini (OpenAI)",
        "Dengeli; akıl yürütme düşük tutulur, ilk yanıt biraz daha geç gelebilir.",
        (r"^openai/gpt-[\d.]+-mini$",),
        "openai/gpt-5.4-mini",
        {"reasoning": {"effort": "low"}},
    ),
    Preset(
        "gpt-nano",
        "GPT nano (OpenAI)",
        "OpenAI'nin en küçük ve en hızlı modeli; basit kararlar için.",
        (r"^openai/gpt-[\d.]+-nano$",),
        "openai/gpt-5.4-nano",
        {"reasoning": {"effort": "low"}},
    ),
    Preset(
        "deepseek-flash",
        "DeepSeek Flash",
        "En düşük maliyet; akıl yürütme kapalı.",
        (r"^deepseek/deepseek-v[\d.]+-flash$",),
        "deepseek/deepseek-v4.1-flash",
        {"reasoning": {"enabled": False}},
    ),
    Preset(
        "qwen-flash",
        "Qwen Flash (Alibaba)",
        "Ucuz ve hızlı bir alternatif.",
        (r"^qwen/qwen[\d.]+-flash$",),
        "qwen/qwen3.8-flash",
    ),
)
PRESET_BY_KEY = {preset.key: preset for preset in PRESETS}
# Ask OpenRouter for the provider with the lowest latency.
ROUTING = {"provider": {"sort": "latency"}}
# Models that rejected the extra request fields; they get plain requests.
_plain_models: set[str] = set()

SYSTEM = f"""Sen Borsa İstanbul hisseleri için hızlı karar veren bir işlem
yöneticisisin. Sana kimliği, tarihi ve fiyat seviyesi gizlenmiş bir hissenin karar
anındaki görünümü verilir. Yalnızca bu verilere dayan; hisseyi veya dönemi tahmin
etmeye çalışma. Gelecek barları göremezsin, karar bar kapanışında verilir ve emir
sonraki barın açılışında gerçekleşir.

Kurallar:
- Pozisyon yoksa karar "AL" ya da "BEKLE"; pozisyon varsa "SAT" ya da "TUT".
- "strateji_ozeti" stratejinin mantığını, "strateji_gecmisi" test öncesinde
  sinyallerinin sonucunu anlatır. Sinyali stratejinin kendi mantığına göre değerlendir:
  stratejinin aradığı koşullar ret nedeni değildir. Örneğin bir dönüş (düşüşte alım)
  stratejisinde düşüş, aşırı satım ve negatif momentum beklenen durumdur.
- Varsayılan olarak strateji sinyalini uygula: giriş sinyalinde AL, çıkış sinyalinde
  SAT. Yalnızca stratejinin mantığıyla açıklanamayan belirgin bir ek risk görürsen
  reddet. Kârlı bir sinyali kaçırmak da zararlı bir işlem kadar hatadır.
- "trend", "momentum", "oynaklik", "hacim", "konum" ve "aktif_sinyaller" stratejiden
  bağımsız genel göstergelerdir; ayarları stratejininkinden farklı olabilir (ör.
  "supertrend_genel" çarpanı 3'tür). Stratejinin kendi durumu "strateji"
  bölümündedir. Genel bir göstergenin strateji sinyaliyle çelişmesi tek başına ret
  nedeni değildir.
- "gunluk" hissenin günlük barlardaki durumunu, "piyasa" XU100'ün eğilimini ve
  büyük hisselerin yüzde kaçının 50 günlük ortalamasının üstünde olduğunu anlatır.
- "benzer_gecmis_sinyaller" doğrudan kanıttır ama az örneğe dayanır: "benzer" bu
  stratejinin bu ana en çok benzeyen geçmiş sinyallerinin gerçekleşmiş sonucu,
  "tumu" tüm geçmiş sinyallerinin sonucudur (maliyet dahil). Sinyaller genelde
  kârlıysa reddetmek kâr kaçırır: yalnızca benzerlerin ortalaması negatifse ve
  başka bir kanıt da bunu destekliyorsa reddet.
- "benzer_gecmis_cikislar" çıkış sinyalinde, benzer geçmiş çıkış sinyallerinden
  sonra pozisyonu birkaç bar daha tutmanın sonucudur ("kazancli_%": tutmanın
  kazandırdığı oran). Tutmanın ortalaması belirgin pozitifse TUT, negatifse SAT.
- "karar_gunlugu" varsa önceki kararlarının sonuçlarını içerir. "kurallar" geçmiş
  sinyallerde sınanmış kurallardır; "bu_sinyale_uyan_kurallar" doluysa ona uy.
  "dersler" önceki testlerden kalan notlardır; kanıtla çelişmedikçe uy.
- Eğitim döneminde güçlü çalışmış sinyallere ağırlık ver, ters çalışmışlara güvenme.
- Gidiş-dönüş işlem maliyeti yaklaşık %{ROUND_TRIP_COST_PCT}.
- Pozisyondayken kârı koru, zararı büyütme; ama tek bir zayıf barda panikle satma.
- "guven": kararının doğru çıkma olasılığı (0-100).
- Yalnızca tek satır JSON yaz, başka hiçbir şey yazma:
  {{"karar": "AL|SAT|BEKLE|TUT", "guven": 0-100, "gerekce": "en fazla 20 kelime"}}"""

LABELS = {"buy": "AL", "sell": "SAT", "hold": "BEKLE", "keep": "TUT"}
_ACTIONS = {"AL": "buy", "SAT": "sell", "BEKLE": "hold", "TUT": "hold"}


# --- Models -----------------------------------------------------------------------


def resolve_model(
    choice: str | None, models: list[dict[str, Any]] | None = None
) -> tuple[str, Preset | None]:
    """Return the model id for a preset key (or a custom model id) and the preset.

    A preset picks the newest model of its family from OpenRouter's list, or its
    fallback id when the list is not available.
    """
    choice = (choice or DEFAULT_PRESET).strip()
    preset = PRESET_BY_KEY.get(choice)
    if preset is None:
        return choice, None
    ids = [model["id"] for model in models or []]
    for pattern in preset.patterns:
        matching = [model_id for model_id in ids if re.match(pattern, model_id)]
        if matching:
            return max(matching, key=_version), preset
    return preset.fallback, preset


def model_info(model_id: str, models: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Return the listed model's details, or just its id."""
    for model in models or []:
        if model["id"] == model_id:
            return model
    return {"id": model_id}


def presets(api_key: str | None) -> dict[str, Any]:
    """List the presets with the model each one resolves to and its prices."""
    try:
        models = list_models(api_key)
        error = None
    except OpenRouterError as failure:
        models, error = None, failure.message
    rows = []
    for preset in PRESETS:
        model_id, _ = resolve_model(preset.key, models)
        info = model_info(model_id, models)
        rows.append(
            {
                **preset.describe(),
                "model": model_id,
                "listed": models is not None and "name" in info,
                "prompt_price": info.get("prompt_price"),
                "completion_price": info.get("completion_price"),
            }
        )
    return {"presets": rows, "default": DEFAULT_PRESET, "error": error}


def estimate_cost(
    info: dict[str, Any], calls: int, prompt_tokens: int = 900, completion: int = 60
) -> float | None:
    """Estimate the USD cost of ``calls`` decisions from per-million prices."""
    prompt_price = info.get("prompt_price")
    completion_price = info.get("completion_price")
    if prompt_price is None or completion_price is None:
        return None
    per_call = (prompt_tokens * prompt_price + completion * completion_price) / 1e6
    return round(per_call * calls, 4)


# --- Snapshot ---------------------------------------------------------------------


def _change(close: np.ndarray, bars: int) -> float | None:
    if len(close) <= bars or not close[-1 - bars]:
        return None
    return number((close[-1] / close[-1 - bars] - 1) * 100, 2)


def _distance(price: float, level: float) -> float | None:
    if level is None or not math.isfinite(level) or not level:
        return None
    return number((price / level - 1) * 100, 2)


def _value(values: np.ndarray, digits: int = 1) -> float | None:
    return number(values[-1], digits) if len(values) else None


def _history_text(stats: dict[str, Any] | None) -> str | None:
    if not stats or not stats.get("events"):
        return None
    h10 = stats.get("h10") or {}
    return (
        f"eğitimde {stats['events']} kez; 10 bar fazla getiri"
        f" %{h10.get('excess_pct')}, isabet %{h10.get('hit_pct')},"
        f" {stats.get('strength')}"
    )


def snapshot(
    frame: pd.DataFrame,
    *,
    benchmark: pd.DataFrame | None = None,
    script: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    training: dict[str, dict[str, Any]] | None = None,
    strategy: dict[str, Any] | None = None,
    journal: dict[str, Any] | None = None,
    interval: str = "1d",
    context: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the last bar of ``frame`` without names, dates or price levels.

    ``frame`` must end at the decision bar: nothing later is ever passed in.
    ``benchmark`` is the index, cut at the same bar. ``script`` holds the
    strategy's event on this bar and its plots; ``position`` the open trade;
    ``training`` each catalog signal's statistics from the training period.
    ``strategy`` adds the strategy's summary and track record
    (``strateji_ozeti``, ``strateji_gecmisi``); ``journal`` the resolved
    outcomes and rules known at this bar (``karar_gunlugu``). ``context``
    holds the daily picture and the market known at this bar (``gunluk``,
    ``piyasa``); ``evidence`` how the strategy's similar past signals ended
    (``benzer_gecmis_sinyaller`` for an entry, ``benzer_gecmis_cikislar`` for
    an exit). Bars other than daily are named in ``zaman_dilimi`` (daily
    views stay unchanged).
    """
    ind = indicators(frame)
    c = ind["close"]
    last = float(c[-1])
    first = c[-RECENT_BARS] if len(c) >= RECENT_BARS else c[0]
    hist = ind["macd_hist"]
    width = ind["bb_upper"][-1] - ind["bb_lower"][-1]
    window = c[-BARS_PER_YEAR.get(interval, 252) :]
    volume20 = ind["volume20"][-1]
    obv = ind["obv"]
    view: dict[str, Any] = {
        "getiri_%": {
            "1_bar": _change(c, 1),
            "5_bar": _change(c, 5),
            "20_bar": _change(c, 20),
            "60_bar": _change(c, 60),
        },
        "trend": {
            "sma20_uzaklik_%": _distance(last, ind["sma20"][-1]),
            "sma50_uzaklik_%": _distance(last, ind["sma50"][-1]),
            "sma200_uzaklik_%": _distance(last, ind["sma200"][-1]),
            "sma50_ustunde_sma200": bool(ind["sma50"][-1] > ind["sma200"][-1])
            if math.isfinite(ind["sma200"][-1])
            else None,
            "adx": _value(ind["adx"]),
            "di_farki": number(ind["plus_di"][-1] - ind["minus_di"][-1], 1),
            "supertrend_genel": "yukarı" if ind["st_dir"][-1] < 0 else "aşağı",
        },
        "momentum": {
            "rsi": _value(ind["rsi"]),
            "macd_hist_%": number(hist[-1] / last * 100, 3),
            "macd_hist_yonu": (
                "artıyor" if len(hist) > 1 and hist[-1] > hist[-2] else "azalıyor"
            ),
            "stoch_k": _value(ind["stoch_k"]),
            "cci": _value(ind["cci"], 0),
            "mfi": _value(ind["mfi"]),
        },
        "oynaklik": {
            "atr_%": number(ind["atr"][-1] / last * 100, 2),
            "bollinger_%b": number((last - ind["bb_lower"][-1]) / width, 2)
            if width
            else None,
            "bollinger_genislik_%": number(width / ind["bb_mid"][-1] * 100, 2)
            if ind["bb_mid"][-1]
            else None,
        },
        "hacim": {
            "hacim_ort20_orani": number(ind["volume"][-1] / volume20, 2)
            if volume20
            else None,
            "obv_20_bar_%": number((obv[-1] - obv[-21]) / abs(obv[-21]) * 100, 1)
            if len(obv) > 21 and obv[-21]
            else None,
        },
        "konum": {
            "52h_zirveden_%": _distance(last, float(np.nanmax(window))),
            "52h_dipten_%": _distance(last, float(np.nanmin(window))),
            "20_bar_zirveden_%": _distance(last, ind["high20"][-1]),
            "20_bar_dipten_%": _distance(last, ind["low20"][-1]),
        },
        "son_20_kapanis_100_tabanli": [
            number(x / first * 100, 1) for x in c[-RECENT_BARS:]
        ],
    }
    if benchmark is not None and len(benchmark) > 21:
        index_close = benchmark["Close"].to_numpy(dtype=float)
        index_change = _change(index_close, 20)
        stock_change = view["getiri_%"]["20_bar"]
        view["endeks"] = {
            "endeks_20_bar_%": index_change,
            "goreli_guc_20_bar": number(stock_change - index_change, 2)
            if stock_change is not None and index_change is not None
            else None,
        }
    active = []
    for key in active_signals(ind):
        item = {"sinyal": key, "aciklama": SIGNAL_BY_KEY[key].label}
        history = _history_text((training or {}).get(key))
        if history:
            item["egitim"] = history
        active.append(item)
    view["aktif_sinyaller"] = active
    for key in ("gunluk", "piyasa"):
        if (context or {}).get(key):
            view[key] = context[key]
    if strategy:
        view.update(strategy)
    if script:
        view["strateji"] = script
    if evidence:
        view.update(evidence)
    view["pozisyon"] = position or {"durum": "yok"}
    if journal:
        view["karar_gunlugu"] = journal
    if interval != "1d":
        view = {"zaman_dilimi": INTERVAL_NAMES.get(interval, interval), **view}
    return view


def script_view(result: Any, close: float, event: str) -> dict[str, Any]:
    """Describe a script's state on its last bar for the snapshot.

    Price-scale plots become distances from the close, so they hide the price.
    """
    plots = {}
    for plot in result.plots[:MAX_PLOTS]:
        value = plot.values[-1] if len(plot.values) else math.nan
        if not math.isfinite(value):
            continue
        if plot.overlay:
            plots[f"{plot.title} (fiyata uzaklık %)"] = _distance(close, value)
        else:
            plots[plot.title] = number(value, 2)
    entries = result.entries
    exits = result.exits
    return {
        "olay": event,
        "giris_sinyali": bool(entries is not None and entries[-1]),
        "cikis_sinyali": bool(exits is not None and exits[-1]),
        "cizimler": plots,
    }


def position_view(entry_price: float, close: float, bars: int, peak: float) -> dict:
    """Describe an open position relative to its entry, without prices."""
    return {
        "durum": "var",
        "kar_%": number((close / entry_price - 1) * 100, 2),
        "bar_sayisi": bars,
        "en_yuksekten_%": number((close / peak - 1) * 100, 2) if peak else None,
    }


# --- Decisions --------------------------------------------------------------------


@dataclass
class Decision:
    """A model's decision on one bar."""

    action: str  # buy, sell or hold
    label: str  # AL, SAT, BEKLE or TUT
    confidence: int | None = None
    reason: str = ""
    latency_ms: int = 0
    cost: float = 0.0
    cached: bool = False
    error: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the decision as a JSON-friendly dict."""
        return asdict(self)


def parse_decision(text: str, holding: bool) -> Decision:
    """Read the model's JSON answer; anything unclear becomes "hold".

    Raises ``ValueError`` when the answer holds no decision at all.
    """
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("Yanıtta JSON yok.")
    try:
        data = json.loads(match.group(0))
    except ValueError as error:
        raise ValueError("Yanıttaki JSON okunamadı.") from error
    raw = str(data.get("karar") or data.get("decision") or "").strip().upper()
    raw = raw.replace("İ", "I")
    action = _ACTIONS.get(raw)
    if action is None:
        raise ValueError(f"Geçersiz karar: {raw or 'boş'}")
    # Only actions that fit the position count; the others mean "do nothing".
    if (action == "buy" and holding) or (action == "sell" and not holding):
        action = "hold"
    label = (
        LABELS[action] if action != "hold" else LABELS["keep" if holding else "hold"]
    )
    try:
        confidence = int(round(float(data.get("guven", data.get("confidence")))))
        confidence = max(0, min(100, confidence))
    except (TypeError, ValueError):
        confidence = None
    reason = " ".join(str(data.get("gerekce") or data.get("reason") or "").split())
    return Decision(action, label, confidence, reason[:240])


class DecisionCache:
    """Decisions on disk, keyed by the model and the exact prompt.

    The cache only saves time and money: if the file cannot be used, lookups
    miss and answers are not stored, but decisions go on.
    """

    def __init__(self, path=None):
        self.path = path or ai_home() / "decisions.sqlite"
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            private_dir(self.path.parent)
        connection = sqlite3.connect(self.path, timeout=10)
        if not self._ready:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS decisions"
                " (key TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL)"
            )
            self._ready = True
        return connection

    def get(self, key: str) -> dict[str, Any] | None:
        """Return a stored answer, or None."""
        with self._lock:
            try:
                connection = self._connect()
                try:
                    row = connection.execute(
                        "SELECT value FROM decisions WHERE key = ?", (key,)
                    ).fetchone()
                finally:
                    connection.close()
            except (OSError, sqlite3.Error):
                return None
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        """Store an answer."""
        with self._lock:
            try:
                connection = self._connect()
                try:
                    with connection:
                        connection.execute(
                            "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?)",
                            (key, json.dumps(value, ensure_ascii=False), time.time()),
                        )
                finally:
                    connection.close()
            except (OSError, sqlite3.Error):
                return


class Decider:
    """Ask a model for decisions; thread-safe, with a cache and a fallback.

    A request the model rejects because of the extra fields (JSON mode,
    reasoning settings) is retried once as a plain request.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        preset: Preset | None = None,
        *,
        json_mode: bool = False,
        cache: DecisionCache | None = None,
        complete: Callable[..., dict[str, Any]] = complete_chat,
    ):
        self.api_key = api_key
        self.model = model
        self.preset = preset
        self.json_mode = json_mode
        self.cache = cache
        self.complete = complete

    def _extra(self) -> dict[str, Any]:
        if self.model in _plain_models:
            return dict(ROUTING)
        extra = {**ROUTING, **(self.preset.extra if self.preset else {})}
        if self.json_mode:
            extra["response_format"] = {"type": "json_object"}
        return extra

    def ask(
        self, messages: list[dict[str, Any]], max_tokens: int = MAX_TOKENS
    ) -> dict[str, Any]:
        """Send one request with the preset's settings (JSON mode, reasoning).

        A model that rejects them gets the request again without them.
        """
        extra = self._extra()
        try:
            return self.complete(
                self.api_key,
                self.model,
                messages,
                temperature=TEMPERATURE,
                max_tokens=max_tokens,
                extra=extra,
                timeout=TIMEOUT,
            )
        except OpenRouterError as error:
            if error.status != 400 or extra == ROUTING:
                raise
            _plain_models.add(self.model)
            return self.complete(
                self.api_key,
                self.model,
                messages,
                temperature=TEMPERATURE,
                max_tokens=max_tokens,
                extra=dict(ROUTING),
                timeout=TIMEOUT,
            )

    def decide(self, view: dict[str, Any]) -> Decision:
        """Decide on one snapshot; errors come back as a "hold" with ``error``.

        A bad key or an account out of credit raises instead: every later
        decision would fail too, and a test of "holds" would mislead.
        """
        holding = (view.get("pozisyon") or {}).get("durum") == "var"
        user = json.dumps(view, ensure_ascii=False, separators=(",", ":"))
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ]
        key = hashlib.sha256(f"{self.model}\n{SYSTEM}\n{user}".encode()).hexdigest()
        stored = self.cache.get(key) if self.cache else None
        if stored:
            decision = parse_decision(stored["text"], holding)
            decision.cached = True
            decision.model = self.model
            return decision
        started = time.perf_counter()
        try:
            answer = self.ask(messages)
            decision = parse_decision(answer["text"], holding)
        except (OpenRouterError, ValueError) as error:
            if isinstance(error, OpenRouterError) and account_blocked(error):
                raise
            message = (
                error.message if isinstance(error, OpenRouterError) else str(error)
            )
            return Decision(
                "hold",
                LABELS["keep" if holding else "hold"],
                error=message,
                latency_ms=round((time.perf_counter() - started) * 1000),
                model=self.model,
            )
        decision.latency_ms = round((time.perf_counter() - started) * 1000)
        decision.cost = answer["usage"]["cost"]
        decision.model = answer.get("model") or self.model
        if self.cache:
            self.cache.put(key, {"text": answer["text"]})
        return decision


class EvidenceDecider:
    """Decide without a model, from how the strategy's similar signals ended.

    An entry is taken when the most similar past entry signals averaged a
    gain after costs (``benzer_gecmis_sinyaller``), or when there is no
    evidence yet. On an exit signal the position is kept when holding on
    after the most similar past exit signals averaged a gain
    (``benzer_gecmis_cikislar``); otherwise it is sold.
    """

    model = "istatistik-filtresi"
    cache = None

    def decide(self, view: dict[str, Any]) -> Decision:
        """Decide on one snapshot from its evidence."""
        holding = (view.get("pozisyon") or {}).get("durum") == "var"
        event = (view.get("strateji") or {}).get("olay")
        if holding:
            if event != "çıkış":
                return Decision("hold", LABELS["keep"], reason="çıkış sinyali yok",
                                model=self.model)  # fmt: skip
            after = (view.get("benzer_gecmis_cikislar") or {}).get("benzer") or {}
            average = after.get("ort_getiri_%")
            if average is not None and average > 0:
                share = after.get("kazancli_%")
                note = f"benzer {after.get('adet')} çıkıştan sonra ort. %{average}"
                return Decision("hold", LABELS["keep"],
                                round(share) if share is not None else None, note,
                                model=self.model)  # fmt: skip
            return Decision("sell", LABELS["sell"], reason="strateji çıkış sinyali",
                            model=self.model)  # fmt: skip
        similar = (view.get("benzer_gecmis_sinyaller") or {}).get("benzer") or {}
        average = similar.get("ort_getiri_%")
        if average is None:
            return Decision("buy", LABELS["buy"], reason="yeterli geçmiş sinyal yok",
                            model=self.model)  # fmt: skip
        confidence = similar.get("kazancli_%")
        confidence = round(confidence) if confidence is not None else None
        note = f"benzer {similar.get('adet')} sinyal ort. %{average}"
        if average > 0:
            return Decision("buy", LABELS["buy"], confidence, note, model=self.model)
        return Decision("hold", LABELS["hold"], confidence, note, model=self.model)


def make_decider(
    api_key: str,
    choice: str | None,
    *,
    use_cache: bool = True,
    complete: Callable[..., dict[str, Any]] = complete_chat,
) -> tuple[Decider, dict[str, Any]]:
    """Build a decider for a preset key or model id; also return the model info."""
    try:
        models = list_models(api_key)
    except OpenRouterError:
        models = None
    model_id, preset = resolve_model(choice, models)
    info = model_info(model_id, models)
    decider = Decider(
        api_key,
        model_id,
        preset,
        json_mode=bool(info.get("json")),
        cache=DecisionCache() if use_cache else None,
        complete=complete,
    )
    return decider, info


# --- Speed test -------------------------------------------------------------------

SAMPLE = {
    "getiri_%": {"1_bar": 1.8, "5_bar": 3.9, "20_bar": 6.2, "60_bar": 11.4},
    "trend": {
        "sma20_uzaklik_%": 2.6,
        "sma50_uzaklik_%": 5.1,
        "sma200_uzaklik_%": 14.3,
        "sma50_ustunde_sma200": True,
        "adx": 27.4,
        "di_farki": 9.8,
        "supertrend_genel": "yukarı",
    },
    "momentum": {
        "rsi": 61.2,
        "macd_hist_%": 0.21,
        "macd_hist_yonu": "artıyor",
        "stoch_k": 78.0,
        "cci": 112,
        "mfi": 64.0,
    },
    "oynaklik": {"atr_%": 2.4, "bollinger_%b": 0.86, "bollinger_genislik_%": 9.7},
    "hacim": {"hacim_ort20_orani": 1.7, "obv_20_bar_%": 8.2},
    "konum": {"52h_zirveden_%": -3.1, "52h_dipten_%": 48.0},
    "aktif_sinyaller": [
        {
            "sinyal": "macd_up",
            "aciklama": "MACD sinyal çizgisini yukarı kesti",
            "egitim": "eğitimde 21 kez; 10 bar fazla getiri %1.2, isabet %62, zayıf",
        }
    ],
    "strateji": {"olay": "giris", "giris_sinyali": True, "cikis_sinyali": False},
    "pozisyon": {"durum": "yok"},
}


def speed_test(
    api_key: str,
    keys: list[str] | None = None,
    *,
    rounds: int = 2,
    complete: Callable[..., dict[str, Any]] = complete_chat,
) -> list[dict[str, Any]]:
    """Time each preset on the same sample decision, without the cache.

    The first request of a model can be slower (connection setup), so each one
    runs ``rounds`` times and reports the best and the average time.
    """
    try:
        models = list_models(api_key)
    except OpenRouterError:
        models = None
    keys = keys or [preset.key for preset in PRESETS]

    def one(key: str) -> dict[str, Any]:
        model_id, preset = resolve_model(key, models)
        info = model_info(model_id, models)
        decider = Decider(
            api_key,
            model_id,
            preset,
            json_mode=bool(info.get("json")),
            complete=complete,
        )
        results = [decider.decide(SAMPLE) for _ in range(max(1, rounds))]
        good = [r for r in results if not r.error]
        times = [r.latency_ms for r in good]
        return {
            "key": key,
            "label": preset.label if preset else key,
            "model": model_id,
            "ok": bool(good),
            "error": next((r.error for r in results if r.error), None),
            "best_ms": min(times) if times else None,
            "average_ms": round(sum(times) / len(times)) if times else None,
            "decision": good[-1].label if good else None,
            "confidence": good[-1].confidence if good else None,
            "reason": good[-1].reason if good else None,
            "cost": round(sum(r.cost for r in results), 6),
            "prompt_price": info.get("prompt_price"),
            "completion_price": info.get("completion_price"),
        }

    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        rows = list(pool.map(one, keys))
    rows.sort(key=lambda r: (not r["ok"], r["average_ms"] or 1e9))
    return rows
