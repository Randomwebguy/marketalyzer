"""Built-in functions, variables and constants of the script language.

The tables here drive both the interpreter (argument binding and conversion)
and the function reference shown in the editor and given to the AI assistant,
so the documentation cannot drift from what actually runs.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from marketalyzer.scripting import ta

REQUIRED = object()


@dataclass(frozen=True)
class Param:
    """A function parameter.

    ``kind`` controls conversion: ``series`` (number series), ``cond`` (boolean
    series), ``int``/``float``/``bool``/``string`` (constants), ``color`` and
    ``any`` (passed through).
    """

    name: str
    kind: str = "series"
    default: Any = REQUIRED


@dataclass(frozen=True)
class Function:
    """A built-in function."""

    name: str
    params: tuple[Param, ...]
    doc: str
    returns: str = "seri"
    impl: Callable[..., Any] | None = None
    category: str = "ta"
    varargs: bool = False
    # Implicit series used when the first argument is left out, e.g. high for
    # ``ta.highest(20)``.
    default_source: str | None = None
    strict: bool = True

    def signature(self) -> str:
        """Return a Pine-style signature such as ``ta.sma(source, length)``."""
        if self.varargs:
            return f"{self.name}(a, b, ...)"
        parts = []
        for param in self.params:
            if param.default is REQUIRED:
                parts.append(param.name)
            else:
                parts.append(f"{param.name}={_show(param.default)}")
        return f"{self.name}({', '.join(parts)})"


def _show(value: Any) -> str:
    if value is None:
        return "na"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if value in SOURCES else f'"{value}"'
    return f"{value:g}" if isinstance(value, float) else str(value)


SOURCES = ("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4")

# TradingView's palette, so pasted scripts look as their authors intended.
PINE_COLORS = {
    "aqua": "#00bcd4",
    "black": "#363a45",
    "blue": "#2962ff",
    "fuchsia": "#e040fb",
    "gray": "#787b86",
    "green": "#4caf50",
    "lime": "#00e676",
    "maroon": "#880e4f",
    "navy": "#311b92",
    "olive": "#808000",
    "orange": "#ff9800",
    "purple": "#9c27b0",
    "red": "#ff5252",
    "silver": "#b2b5be",
    "teal": "#00897b",
    "white": "#ffffff",
    "yellow": "#ffeb3b",
}
CONSTANTS: dict[str, Any] = {
    "math.pi": math.pi,
    "math.e": math.e,
    "math.phi": (1 + math.sqrt(5)) / 2,
    "math.rphi": 2 / (1 + math.sqrt(5)),
    "strategy.long": "long",
    "strategy.short": "short",
    "strategy.fixed": "fixed",
    "strategy.cash": "cash",
    "strategy.percent_of_equity": "percent_of_equity",
    "dayofweek.sunday": 1,
    "dayofweek.monday": 2,
    "dayofweek.tuesday": 3,
    "dayofweek.wednesday": 4,
    "dayofweek.thursday": 5,
    "dayofweek.friday": 6,
    "dayofweek.saturday": 7,
    "syminfo.currency": "TRY",
    "syminfo.timezone": "Europe/Istanbul",
    "syminfo.type": "stock",
    **{f"color.{name}": value for name, value in PINE_COLORS.items()},
}
# Members of these namespaces are display options; their value is their name.
ENUM_NAMESPACES = {
    "plot",
    "shape",
    "location",
    "size",
    "hline",
    "display",
    "extend",
    "xloc",
    "yloc",
    "position",
    "text",
    "font",
    "format",
    "scale",
    "barmerge",
    "alert",
    "order",
    "currency",
    "adjustment",
    "session",
    "label",
    "line",
}
DRAWING_NAMESPACES = {"label", "line", "box", "table", "linefill", "polyline", "chart"}
UNSUPPORTED_NAMESPACES = {
    "array": "Diziler (array) desteklenmiyor.",
    "matrix": "Matrisler desteklenmiyor.",
    "map": "Map veri yapısı desteklenmiyor.",
    "request": "request.* (başka sembol/zaman dilimi verisi) desteklenmiyor;"
    " script yalnızca seçili sembolün verisini görür.",
    "ticker": "ticker.* desteklenmiyor.",
    "runtime": "runtime.* desteklenmiyor.",
    "log": "log.* desteklenmiyor.",
}

# Variables documented for the reference. Their values come from the runtime.
VARIABLES = {
    "open": "Barın açılış fiyatı.",
    "high": "Barın en yüksek fiyatı.",
    "low": "Barın en düşük fiyatı.",
    "close": "Barın kapanış fiyatı.",
    "volume": "Barın işlem hacmi (lot).",
    "hl2": "(high + low) / 2",
    "hlc3": "(high + low + close) / 3",
    "ohlc4": "(open + high + low + close) / 4",
    "hlcc4": "(high + low + close + close) / 4",
    "bar_index": "Barın sıra numarası (0'dan başlar).",
    "last_bar_index": "Son barın sıra numarası.",
    "time": "Bar açılış zamanı (UNIX milisaniye).",
    "year": "Yıl (İstanbul saati).",
    "month": "Ay (1-12).",
    "weekofyear": "Yılın haftası.",
    "dayofmonth": "Ayın günü.",
    "dayofweek": "Haftanın günü (dayofweek.monday = 2 ... Pine ile aynı).",
    "hour": "Saat (İstanbul).",
    "minute": "Dakika.",
    "ta.tr": "Gerçek aralık (true range); ilk bar na.",
    "ta.obv": "On-balance volume.",
    "ta.vwap": "Günlük sıfırlanan hacim ağırlıklı ortalama fiyat (hlc3).",
    "ta.accdist": "Birikim/dağıtım çizgisi.",
    "syminfo.ticker": "Sembol kodu, ör. THYAO.",
    "syminfo.tickerid": "BIST:THYAO biçiminde sembol.",
    "syminfo.mintick": "Son fiyattaki BIST fiyat adımı.",
    "timeframe.period": "Zaman dilimi: D, W, M, 60, 30, 15, 5, 1 ...",
    "timeframe.isdaily": "Günlük barlarda true.",
    "timeframe.isintraday": "Gün içi barlarda true.",
    "barstate.isfirst": "İlk barda true.",
    "barstate.islast": "Son barda true.",
    "barstate.isconfirmed": "Bar kapanmışsa true (kapanmış barlar kullanıldığı için hep true).",
    "strategy.position_size": "Açık pozisyon (lot birimi yerine 1/0, sinyallerden yaklaşık hesaplanır).",
    "strategy.position_avg_price": "Açık pozisyonun yaklaşık giriş fiyatı (giriş barının açılışı).",
    "strategy.opentrades": "Açık işlem sayısı (0 ya da 1).",
}


def _src(name: str = "source", default: Any = REQUIRED) -> Param:
    return Param(name, "series", default)


def _len(name: str = "length", default: Any = REQUIRED) -> Param:
    return Param(name, "int", default)


def _num(name: str, default: Any = REQUIRED) -> Param:
    return Param(name, "float", default)


FUNCTIONS: dict[str, Function] = {}


def _add(*functions: Function) -> None:
    for function in functions:
        FUNCTIONS[function.name] = function


def _ta(name, params, doc, impl, returns="seri", **extra) -> Function:
    return Function(name, tuple(params), doc, returns, impl, "ta", **extra)


def _math(name, params, doc, impl, returns="seri", **extra) -> Function:
    return Function(name, tuple(params), doc, returns, impl, "math", **extra)


# --- ta.* ------------------------------------------------------------------

_add(
    _ta(
        "ta.sma",
        [_src(), _len()],
        "Basit hareketli ortalama.",
        lambda c, a: ta.sma(a["source"], a["length"]),
    ),
    _ta(
        "ta.ema",
        [_src(), _len()],
        "Üssel hareketli ortalama.",
        lambda c, a: ta.ema(a["source"], a["length"]),
    ),
    _ta(
        "ta.rma",
        [_src(), _len()],
        "Wilder ortalaması (RSI/ATR'nin kullandığı).",
        lambda c, a: ta.rma(a["source"], a["length"]),
    ),
    _ta(
        "ta.wma",
        [_src(), _len()],
        "Ağırlıklı hareketli ortalama.",
        lambda c, a: ta.wma(a["source"], a["length"]),
    ),
    _ta(
        "ta.hma",
        [_src(), _len()],
        "Hull hareketli ortalaması.",
        lambda c, a: ta.hma(a["source"], a["length"]),
    ),
    _ta(
        "ta.vwma",
        [_src(), _len()],
        "Hacim ağırlıklı hareketli ortalama.",
        lambda c, a: ta.vwma(a["source"], c.volume, a["length"]),
    ),
    _ta(
        "ta.swma",
        [_src()],
        "4 barlık simetrik ağırlıklı ortalama.",
        lambda c, a: ta.swma(a["source"]),
    ),
    _ta(
        "ta.alma",
        [_src("series"), _len(), _num("offset", 0.85), _num("sigma", 6.0)],
        "Arnaud Legoux hareketli ortalaması.",
        lambda c, a: ta.alma(a["series"], a["length"], a["offset"], a["sigma"]),
    ),
    _ta(
        "ta.linreg",
        [_src(), _len(), _len("offset", 0)],
        "Doğrusal regresyon eğrisi.",
        lambda c, a: ta.linreg(a["source"], a["length"], a["offset"]),
    ),
    _ta(
        "ta.rsi",
        [_src(), _len()],
        "Göreceli güç endeksi (0-100).",
        lambda c, a: ta.rsi(a["source"], a["length"]),
    ),
    _ta(
        "ta.macd",
        [_src(), _len("fastlen"), _len("slowlen"), _len("siglen")],
        "MACD; [macd, sinyal, histogram] döndürür.",
        lambda c, a: ta.macd(a["source"], a["fastlen"], a["slowlen"], a["siglen"]),
        "[seri, seri, seri]",
    ),
    _ta(
        "ta.stoch",
        [_src(), _src("high"), _src("low"), _len()],
        "Stokastik %K (yumuşatılmamış). %D için ta.sma ile yumuşatın.",
        lambda c, a: ta.stoch(a["source"], a["high"], a["low"], a["length"]),
    ),
    _ta(
        "ta.cci",
        [_src(), _len()],
        "Emtia kanal endeksi.",
        lambda c, a: ta.cci(a["source"], a["length"]),
    ),
    _ta(
        "ta.cmo",
        [_src("series"), _len()],
        "Chande momentum osilatörü.",
        lambda c, a: ta.cmo(a["series"], a["length"]),
    ),
    _ta(
        "ta.mfi",
        [_src("series"), _len()],
        "Para akışı endeksi (genelde hlc3 ile).",
        lambda c, a: ta.mfi(a["series"], c.volume, a["length"]),
    ),
    _ta(
        "ta.wpr",
        [_len()],
        "Williams %R (-100..0).",
        lambda c, a: ta.wpr(c.high, c.low, c.close, a["length"]),
    ),
    _ta(
        "ta.mom",
        [_src(), _len()],
        "Momentum: source - source[length].",
        lambda c, a: ta.mom(a["source"], a["length"]),
    ),
    _ta(
        "ta.roc",
        [_src(), _len()],
        "Değişim oranı (%).",
        lambda c, a: ta.roc(a["source"], a["length"]),
    ),
    _ta(
        "ta.change",
        [Param("source", "any"), _len("length", 1)],
        "source - source[length]; mantıksal seride değişti mi.",
        lambda c, a: ta.change(a["source"], a["length"]),
    ),
    _ta(
        "ta.tr",
        [Param("handle_na", "bool", False)],
        "Gerçek aralık; handle_na=true ise ilk bar high-low.",
        lambda c, a: ta.tr(c.high, c.low, c.close, a["handle_na"]),
    ),
    _ta(
        "ta.atr",
        [_len()],
        "Ortalama gerçek aralık.",
        lambda c, a: ta.atr(c.high, c.low, c.close, a["length"]),
    ),
    _ta(
        "ta.dmi",
        [_len("diLength"), _len("adxSmoothing")],
        "Yön göstergesi; [+DI, -DI, ADX] döndürür.",
        lambda c, a: ta.dmi(c.high, c.low, c.close, a["diLength"], a["adxSmoothing"]),
        "[seri, seri, seri]",
    ),
    _ta(
        "ta.supertrend",
        [_num("factor"), _len("atrPeriod")],
        "Supertrend; [çizgi, yön] döndürür, yön -1 ise yükseliş.",
        lambda c, a: ta.supertrend(c.high, c.low, c.close, a["factor"], a["atrPeriod"]),
        "[seri, seri]",
    ),
    _ta(
        "ta.sar",
        [_num("start"), _num("inc"), _num("max")],
        "Parabolik SAR.",
        lambda c, a: ta.sar(c.high, c.low, a["start"], a["inc"], a["max"]),
    ),
    _ta(
        "ta.bb",
        [_src("series"), _len(), _num("mult")],
        "Bollinger bantları; [orta, üst, alt] döndürür.",
        lambda c, a: ta.bb(a["series"], a["length"], a["mult"]),
        "[seri, seri, seri]",
    ),
    _ta(
        "ta.bbw",
        [_src("series"), _len(), _num("mult")],
        "Bollinger bant genişliği.",
        lambda c, a: ta.bbw(a["series"], a["length"], a["mult"]),
    ),
    _ta(
        "ta.kc",
        [_src("series"), _len(), _num("mult"), Param("useTrueRange", "bool", True)],
        "Keltner kanalı; [orta, üst, alt] döndürür.",
        lambda c, a: ta.kc(
            a["series"],
            c.high,
            c.low,
            c.close,
            a["length"],
            a["mult"],
            a["useTrueRange"],
        ),
        "[seri, seri, seri]",
    ),
    _ta(
        "ta.highest",
        [_src(), _len()],
        "Son length barın en yükseği (source verilmezse high).",
        lambda c, a: ta.highest(a["source"], a["length"]),
        default_source="high",
    ),
    _ta(
        "ta.lowest",
        [_src(), _len()],
        "Son length barın en düşüğü (source verilmezse low).",
        lambda c, a: ta.lowest(a["source"], a["length"]),
        default_source="low",
    ),
    _ta(
        "ta.highestbars",
        [_src(), _len()],
        "En yüksek bara uzaklık (0 veya negatif).",
        lambda c, a: ta.highestbars(a["source"], a["length"]),
        default_source="high",
    ),
    _ta(
        "ta.lowestbars",
        [_src(), _len()],
        "En düşük bara uzaklık (0 veya negatif).",
        lambda c, a: ta.lowestbars(a["source"], a["length"]),
        default_source="low",
    ),
    _ta(
        "ta.stdev",
        [_src(), _len(), Param("biased", "bool", True)],
        "Standart sapma.",
        lambda c, a: ta.stdev(a["source"], a["length"], a["biased"]),
    ),
    _ta(
        "ta.variance",
        [_src(), _len(), Param("biased", "bool", True)],
        "Varyans.",
        lambda c, a: ta.variance(a["source"], a["length"], a["biased"]),
    ),
    _ta(
        "ta.dev",
        [_src(), _len()],
        "Ortalama mutlak sapma.",
        lambda c, a: ta.dev(a["source"], a["length"]),
    ),
    _ta(
        "ta.median",
        [_src(), _len()],
        "Hareketli medyan.",
        lambda c, a: ta.median(a["source"], a["length"]),
    ),
    _ta(
        "ta.percentrank",
        [_src(), _len()],
        "Önceki length değerin yüzde kaçı şimdikinden küçük/eşit.",
        lambda c, a: ta.percentrank(a["source"], a["length"]),
    ),
    _ta(
        "ta.correlation",
        [_src("source1"), _src("source2"), _len()],
        "İki seri arasındaki korelasyon.",
        lambda c, a: ta.correlation(a["source1"], a["source2"], a["length"]),
    ),
    _ta(
        "ta.range",
        [_src(), _len()],
        "Son length barda en yüksek - en düşük.",
        lambda c, a: (
            ta.highest(a["source"], a["length"]) - ta.lowest(a["source"], a["length"])
        ),
    ),
    _ta("ta.cum", [_src()], "Kümülatif toplam.", lambda c, a: ta.cum(a["source"])),
    _ta(
        "ta.max",
        [_src()],
        "Şimdiye kadarki en yüksek değer.",
        lambda c, a: ta.running_max(a["source"]),
    ),
    _ta(
        "ta.min",
        [_src()],
        "Şimdiye kadarki en düşük değer.",
        lambda c, a: ta.running_min(a["source"]),
    ),
    _ta(
        "ta.crossover",
        [_src("source1"), _src("source2")],
        "source1, source2'yi yukarı kesti.",
        lambda c, a: ta.crossover(a["source1"], a["source2"]),
        "mantıksal",
    ),
    _ta(
        "ta.crossunder",
        [_src("source1"), _src("source2")],
        "source1, source2'yi aşağı kesti.",
        lambda c, a: ta.crossunder(a["source1"], a["source2"]),
        "mantıksal",
    ),
    _ta(
        "ta.cross",
        [_src("source1"), _src("source2")],
        "İki seri herhangi bir yönde kesişti.",
        lambda c, a: ta.cross(a["source1"], a["source2"]),
        "mantıksal",
    ),
    _ta(
        "ta.rising",
        [_src(), _len()],
        "source önceki length değerin hepsinden büyük.",
        lambda c, a: ta.rising(a["source"], a["length"]),
        "mantıksal",
    ),
    _ta(
        "ta.falling",
        [_src(), _len()],
        "source önceki length değerin hepsinden küçük.",
        lambda c, a: ta.falling(a["source"], a["length"]),
        "mantıksal",
    ),
    _ta(
        "ta.barssince",
        [Param("condition", "cond")],
        "Koşulun son doğru olduğu bardan beri geçen bar sayısı.",
        lambda c, a: ta.barssince(a["condition"]),
    ),
    _ta(
        "ta.valuewhen",
        [Param("condition", "cond"), _src(), _len("occurrence", 0)],
        "Koşulun doğru olduğu n'inci son bardaki source değeri.",
        lambda c, a: ta.valuewhen(a["condition"], a["source"], a["occurrence"]),
    ),
    _ta(
        "ta.pivothigh",
        [_src(), _len("leftbars"), _len("rightbars")],
        "Tepe noktası; rightbars bar sonra onaylanınca değer verir.",
        lambda c, a: ta.pivothigh(a["source"], a["leftbars"], a["rightbars"]),
        default_source="high",
    ),
    _ta(
        "ta.pivotlow",
        [_src(), _len("leftbars"), _len("rightbars")],
        "Dip noktası; rightbars bar sonra onaylanınca değer verir.",
        lambda c, a: ta.pivotlow(a["source"], a["leftbars"], a["rightbars"]),
        default_source="low",
    ),
    _ta(
        "ta.vwap",
        [_src()],
        "Günlük sıfırlanan VWAP.",
        lambda c, a: ta.vwap(a["source"], c.volume, c.sessions),
    ),
)

# --- math.* and helpers ------------------------------------------------------


def _unary(func: Callable[[np.ndarray], np.ndarray]) -> Callable:
    def apply(context, args):
        with np.errstate(all="ignore"):
            value = func(np.asarray(args["number"], dtype=float))
        return _cleaned(value)

    return apply


def _cleaned(value):
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            value = float(value)
        else:
            value = value.astype(float)
            value[~np.isfinite(value)] = np.nan
            return value
    return value if math.isfinite(value) else float("nan")


def _round(context, args):
    number = np.asarray(args["number"], dtype=float)
    precision = args["precision"]
    value = np.round(number, precision) if precision else np.floor(number + 0.5)
    return _cleaned(value)


def _reduce(func: Callable) -> Callable:
    def apply(context, values):
        arrays = [np.asarray(value, dtype=float) for value in values]
        with np.errstate(all="ignore"):
            out = arrays[0]
            for value in arrays[1:]:
                out = func(out, value)
        return _cleaned(out)

    return apply


def _average(context, values):
    arrays = [np.asarray(value, dtype=float) for value in values]
    return _cleaned(sum(arrays[1:], arrays[0]) / len(arrays))


_add(
    _math("math.abs", [Param("number")], "Mutlak değer.", _unary(np.abs)),
    _math("math.sqrt", [Param("number")], "Karekök.", _unary(np.sqrt)),
    _math("math.log", [Param("number")], "Doğal logaritma.", _unary(np.log)),
    _math("math.log10", [Param("number")], "10 tabanında logaritma.", _unary(np.log10)),
    _math("math.exp", [Param("number")], "e üzeri.", _unary(np.exp)),
    _math("math.floor", [Param("number")], "Aşağı yuvarla.", _unary(np.floor)),
    _math("math.ceil", [Param("number")], "Yukarı yuvarla.", _unary(np.ceil)),
    _math("math.sign", [Param("number")], "İşaret: -1, 0 veya 1.", _unary(np.sign)),
    _math("math.sin", [Param("number")], "Sinüs (radyan).", _unary(np.sin)),
    _math("math.cos", [Param("number")], "Kosinüs (radyan).", _unary(np.cos)),
    _math("math.tan", [Param("number")], "Tanjant (radyan).", _unary(np.tan)),
    _math(
        "math.round",
        [Param("number"), Param("precision", "int", 0)],
        "Yuvarla; precision ondalık basamak.",
        _round,
    ),
    _math(
        "math.pow",
        [Param("base"), Param("exponent")],
        "Üs alma.",
        lambda c, a: _cleaned(
            np.power(np.asarray(a["base"], dtype=float), a["exponent"])
        ),
    ),
    _math(
        "math.max",
        (),
        "En büyük değer (iki ya da daha fazla argüman).",
        _reduce(np.fmax),
        varargs=True,
    ),
    _math(
        "math.min",
        (),
        "En küçük değer (iki ya da daha fazla argüman).",
        _reduce(np.fmin),
        varargs=True,
    ),
    _math("math.avg", (), "Argümanların ortalaması.", _average, varargs=True),
    _math(
        "math.sum",
        [_src(), _len()],
        "Son length barın toplamı.",
        lambda c, a: ta.math_sum(a["source"], a["length"]),
    ),
    _math(
        "math.todegrees",
        [Param("radians")],
        "Radyanı dereceye çevir.",
        lambda c, a: _cleaned(np.degrees(np.asarray(a["radians"], dtype=float))),
    ),
    _math(
        "math.toradians",
        [Param("degrees")],
        "Dereceyi radyana çevir.",
        lambda c, a: _cleaned(np.radians(np.asarray(a["degrees"], dtype=float))),
    ),
)


def _nz(context, args):
    source, replacement = args["source"], args["replacement"]
    if isinstance(source, np.ndarray):
        if source.dtype == bool:
            return source
        return np.where(np.isnan(source.astype(float)), replacement, source)
    if source is None or (isinstance(source, float) and math.isnan(source)):
        return replacement
    return source


def _na(context, args):
    value = args["x"]
    if isinstance(value, np.ndarray):
        if value.dtype == bool:
            return np.zeros(len(value), dtype=bool)
        if value.dtype == object:
            return np.array([item is None for item in value])
        return np.isnan(value.astype(float))
    return value is None or (isinstance(value, float) and math.isnan(value))


def _cast(kind: str) -> Callable:
    def apply(context, args):
        value = args["x"]
        if kind == "bool":
            if isinstance(value, np.ndarray):
                return (value != 0) & ~np.isnan(value.astype(float))
            return bool(value) and not (isinstance(value, float) and math.isnan(value))
        if isinstance(value, np.ndarray):
            value = value.astype(float)
            return np.trunc(value) if kind == "int" else value
        if isinstance(value, float) and math.isnan(value):
            return value
        return int(value) if kind == "int" else float(value)

    return apply


def _color_new(context, args):
    color = args["color"]
    transp = args["transp"]
    if not isinstance(color, str) or not color.startswith("#"):
        return color
    alpha = round(255 * (1 - max(0.0, min(100.0, float(transp))) / 100))
    return f"{color[:7]}{alpha:02x}"


def _color_rgb(context, args):
    channels = [int(max(0, min(255, args[name]))) for name in ("red", "green", "blue")]
    alpha = round(255 * (1 - max(0.0, min(100.0, float(args["transp"]))) / 100))
    return "#" + "".join(f"{value:02x}" for value in channels) + f"{alpha:02x}"


def _tostring(context, args):
    value = args["value"]
    if isinstance(value, np.ndarray):
        return "{{değer}}"
    if isinstance(value, float):
        return "NaN" if math.isnan(value) else f"{value:g}"
    return str(value)


_add(
    Function(
        "nz",
        (Param("source", "any"), Param("replacement", "any", 0)),
        "Eksik (na) değeri replacement ile değiştir.",
        "seri",
        _nz,
        "genel",
    ),
    Function(
        "na", (Param("x", "any"),), "Değer eksik mi (na).", "mantıksal", _na, "genel"
    ),
    Function(
        "fixnan",
        (_src(),),
        "Eksik değerleri son bilinen değerle doldur.",
        "seri",
        lambda c, a: ta.fixnan(a["source"]),
        "genel",
    ),
    Function(
        "int",
        (Param("x", "any"),),
        "Tam sayıya çevir (kesirli kısmı at).",
        "seri",
        _cast("int"),
        "genel",
    ),
    Function(
        "float",
        (Param("x", "any"),),
        "Ondalık sayıya çevir.",
        "seri",
        _cast("float"),
        "genel",
    ),
    Function(
        "bool",
        (Param("x", "any"),),
        "Mantıksal değere çevir.",
        "mantıksal",
        _cast("bool"),
        "genel",
    ),
    Function(
        "color.new",
        (Param("color", "color"), Param("transp", "float", 0)),
        "Rengi şeffaflıkla (0-100) döndür.",
        "renk",
        _color_new,
        "genel",
    ),
    Function(
        "color.rgb",
        (
            Param("red", "float"),
            Param("green", "float"),
            Param("blue", "float"),
            Param("transp", "float", 0),
        ),
        "RGB'den renk oluştur.",
        "renk",
        _color_rgb,
        "genel",
    ),
    Function(
        "str.tostring",
        (Param("value", "any"), Param("format", "any", None)),
        "Değeri metne çevir (yalnızca sabitler için anlamlı).",
        "metin",
        _tostring,
        "genel",
        strict=False,
    ),
)

# --- Inputs ------------------------------------------------------------------

_INPUT_EXTRAS = (
    Param("tooltip", "any", None),
    Param("inline", "any", None),
    Param("group", "any", None),
    Param("confirm", "any", False),
    Param("display", "any", None),
)


def _input(name: str, params: tuple[Param, ...], doc: str, returns: str) -> Function:
    return Function(
        name, (*params, *_INPUT_EXTRAS), doc, returns, None, "input", strict=False
    )


_add(
    _input(
        "input",
        (Param("defval", "any"), Param("title", "any", None)),
        "Genel giriş; tipi varsayılan değerden anlaşılır.",
        "sabit",
    ),
    _input(
        "input.int",
        (
            Param("defval", "any"),
            Param("title", "any", None),
            Param("minval", "any", None),
            Param("maxval", "any", None),
            Param("step", "any", None),
            Param("options", "any", None),
        ),
        "Tam sayı parametre. minval/maxval/step optimizasyon aralığını belirler.",
        "tam sayı",
    ),
    _input(
        "input.float",
        (
            Param("defval", "any"),
            Param("title", "any", None),
            Param("minval", "any", None),
            Param("maxval", "any", None),
            Param("step", "any", None),
            Param("options", "any", None),
        ),
        "Ondalık parametre.",
        "ondalık",
    ),
    _input(
        "input.bool",
        (Param("defval", "any"), Param("title", "any", None)),
        "Açık/kapalı parametre.",
        "mantıksal",
    ),
    _input(
        "input.string",
        (
            Param("defval", "any"),
            Param("title", "any", None),
            Param("options", "any", None),
        ),
        "Metin parametre; options ile seçenek listesi.",
        "metin",
    ),
    _input(
        "input.source",
        (Param("defval", "any"), Param("title", "any", None)),
        "Kaynak seri seçimi (close, open, hl2 ...).",
        "seri",
    ),
    _input(
        "input.price",
        (Param("defval", "any"), Param("title", "any", None)),
        "Fiyat parametresi.",
        "ondalık",
    ),
    _input(
        "input.color",
        (Param("defval", "any"), Param("title", "any", None)),
        "Renk (yalnızca görünüm).",
        "renk",
    ),
    _input(
        "input.timeframe",
        (Param("defval", "any"), Param("title", "any", None)),
        "Zaman dilimi (yalnızca varsayılan kullanılır).",
        "metin",
    ),
    _input(
        "input.session",
        (Param("defval", "any"), Param("title", "any", None)),
        "Seans (yalnızca varsayılan kullanılır).",
        "metin",
    ),
    _input(
        "input.symbol",
        (Param("defval", "any"), Param("title", "any", None)),
        "Sembol (yalnızca varsayılan kullanılır).",
        "metin",
    ),
    _input(
        "input.text_area",
        (Param("defval", "any"), Param("title", "any", None)),
        "Uzun metin.",
        "metin",
    ),
)

# --- Statements with effects ---------------------------------------------------


def _effect(name: str, params: tuple[Param, ...], doc: str, category: str) -> Function:
    return Function(name, params, doc, "-", None, category, strict=False)


_add(
    _effect(
        "indicator",
        (
            Param("title", "any"),
            Param("shorttitle", "any", None),
            Param("overlay", "bool", False),
        ),
        "Scripti gösterge olarak bildirir. overlay=true ise fiyat grafiğine çizilir.",
        "bildirim",
    ),
    _effect(
        "study",
        (
            Param("title", "any"),
            Param("shorttitle", "any", None),
            Param("overlay", "bool", False),
        ),
        "indicator() ile aynı (eski sürüm adı).",
        "bildirim",
    ),
    _effect(
        "strategy",
        (
            Param("title", "any"),
            Param("shorttitle", "any", None),
            Param("overlay", "bool", False),
            Param("initial_capital", "any", None),
            Param("default_qty_type", "any", None),
            Param("default_qty_value", "any", None),
            Param("pyramiding", "any", 0),
        ),
        "Scripti strateji olarak bildirir; backtest, optimizasyon ve paper trading'de kullanılabilir. Komisyon ve vergi her zaman BIST maliyetleridir. default_qty_type verilmezse tüm özsermaye kullanılır.",
        "bildirim",
    ),
    _effect(
        "plot",
        (
            Param("series", "any"),
            Param("title", "any", None),
            Param("color", "any", None),
            Param("linewidth", "any", 1),
            Param("style", "any", "plot.style_line"),
            Param("force_overlay", "bool", False),
        ),
        "Seriyi grafiğe çizer.",
        "çizim",
    ),
    _effect(
        "plotshape",
        (
            Param("series", "any"),
            Param("title", "any", None),
            Param("style", "any", "shape.xcross"),
            Param("location", "any", "location.abovebar"),
            Param("color", "any", None),
            Param("text", "any", None),
        ),
        "Koşulun doğru olduğu barlara işaret koyar.",
        "çizim",
    ),
    _effect(
        "plotchar",
        (
            Param("series", "any"),
            Param("title", "any", None),
            Param("char", "any", "★"),
            Param("location", "any", "location.abovebar"),
            Param("color", "any", None),
            Param("text", "any", None),
        ),
        "Koşulun doğru olduğu barlara karakter koyar.",
        "çizim",
    ),
    _effect(
        "hline",
        (
            Param("price", "float"),
            Param("title", "any", None),
            Param("color", "any", None),
        ),
        "Sabit yatay çizgi (ör. RSI 70/30).",
        "çizim",
    ),
    _effect(
        "bgcolor",
        (Param("color", "any"), Param("title", "any", None)),
        "Arka plan rengi (yok sayılır).",
        "çizim",
    ),
    _effect("barcolor", (Param("color", "any"),), "Bar rengi (yok sayılır).", "çizim"),
    _effect(
        "fill",
        (Param("hline1", "any"), Param("hline2", "any")),
        "İki çizgi arası dolgu (yok sayılır).",
        "çizim",
    ),
    _effect(
        "alertcondition",
        (
            Param("condition", "any"),
            Param("title", "any", None),
            Param("message", "any", None),
        ),
        "Alarm koşulu tanımlar; son tetiklenme zamanları raporlanır.",
        "alarm",
    ),
    _effect(
        "alert",
        (Param("message", "any"), Param("freq", "any", None)),
        "Koşul bloğu içinde alarm üretir.",
        "alarm",
    ),
    _effect(
        "strategy.entry",
        (
            Param("id", "any"),
            Param("direction", "any"),
            Param("qty", "any", None),
            Param("limit", "any", None),
            Param("stop", "any", None),
            Param("oca_name", "any", None),
            Param("oca_type", "any", None),
            Param("comment", "any", None),
            Param("alert_message", "any", None),
            Param("when", "any", True),
        ),
        "Pozisyona giriş. Emir bir sonraki barın açılışında gerçekleşir. strategy.short girişleri BIST'te açığa satış olmadığı için uzun pozisyonu kapatır.",
        "strateji",
    ),
    _effect(
        "strategy.close",
        (
            Param("id", "any", None),
            Param("comment", "any", None),
            Param("qty", "any", None),
            Param("qty_percent", "any", None),
            Param("alert_message", "any", None),
            Param("immediately", "any", False),
            Param("when", "any", True),
        ),
        "Pozisyonu kapatır.",
        "strateji",
    ),
    _effect(
        "strategy.close_all",
        (
            Param("comment", "any", None),
            Param("alert_message", "any", None),
            Param("immediately", "any", False),
            Param("when", "any", True),
        ),
        "Tüm pozisyonları kapatır.",
        "strateji",
    ),
    _effect(
        "strategy.exit",
        (
            Param("id", "any"),
            Param("from_entry", "any", None),
            Param("qty", "any", None),
            Param("qty_percent", "any", None),
            Param("profit", "any", None),
            Param("limit", "any", None),
            Param("loss", "any", None),
            Param("stop", "any", None),
            Param("trail_price", "any", None),
            Param("trail_points", "any", None),
            Param("trail_offset", "any", None),
            Param("comment", "any", None),
            Param("when", "any", True),
        ),
        "Zarar durdur (stop/loss) ve kâr al (limit/profit) seviyeleri. profit/loss BIST fiyat adımı (tick) cinsindendir.",
        "strateji",
    ),
    _effect(
        "strategy.cancel",
        (Param("id", "any"), Param("when", "any", True)),
        "Bekleyen girişi iptal eder (yok sayılır).",
        "strateji",
    ),
    _effect(
        "strategy.cancel_all",
        (Param("when", "any", True),),
        "Bekleyen girişleri iptal eder (yok sayılır).",
        "strateji",
    ),
)

EFFECTS = {
    name
    for name, function in FUNCTIONS.items()
    if function.impl is None and function.category != "input"
}
DECLARATIONS = {"indicator", "study", "strategy"}


@dataclass
class ReferenceEntry:
    """One entry of the language reference."""

    name: str
    signature: str
    doc: str
    returns: str
    category: str
    examples: list[str] = field(default_factory=list)


CATEGORY_TITLES = {
    "bildirim": "Bildirim",
    "input": "Parametreler",
    "ta": "Teknik analiz",
    "math": "Matematik",
    "genel": "Genel",
    "çizim": "Çizim",
    "strateji": "Strateji",
    "alarm": "Alarm",
    "değişken": "Değişkenler",
}


def reference() -> list[dict]:
    """Return the language reference, grouped by category, for the UI and AI."""
    entries = [
        {
            "name": function.name,
            "signature": function.signature(),
            "doc": function.doc,
            "returns": function.returns,
            "category": function.category,
        }
        for function in FUNCTIONS.values()
    ]
    entries += [
        {
            "name": name,
            "signature": name,
            "doc": doc,
            "returns": "seri",
            "category": "değişken",
        }
        for name, doc in VARIABLES.items()
    ]
    order = list(CATEGORY_TITLES)
    return sorted(entries, key=lambda e: (order.index(e["category"]), e["name"]))
