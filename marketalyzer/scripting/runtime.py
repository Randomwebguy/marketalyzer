"""Compile and run scripts over a frame of price bars.

Scripts run in two phases. Most statements only combine whole series, so they
are evaluated once, vectorized with numpy, over every bar: fast enough to run
inside optimizations. Statements whose value depends on their own past
(``var`` variables, ``x := nz(x[1]) + 1``, ``strategy.position_size``) are run
bar by bar afterwards, the way Pine runs every script; the series they read from
the first phase are precomputed, so only the stateful parts are interpreted.

Both phases are causal: nothing a script computes on a bar can depend on a
later bar, so backtests cannot look ahead.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer.backtest.costs import tick_size
from marketalyzer.scripting import ta
from marketalyzer.scripting.builtins import (
    CONSTANTS,
    DECLARATIONS,
    DRAWING_NAMESPACES,
    EFFECTS,
    ENUM_NAMESPACES,
    FUNCTIONS,
    REQUIRED,
    SOURCES,
    UNSUPPORTED_NAMESPACES,
    Function,
)
from marketalyzer.scripting.errors import ScriptError
from marketalyzer.scripting.parser import (
    Assign,
    Binary,
    Bool,
    Call,
    Color,
    ExprStmt,
    FuncDef,
    If,
    Index,
    Na,
    Name,
    Node,
    Num,
    Reassign,
    Str,
    Ternary,
    TupleLit,
    Unary,
    parse,
    walk,
)

NAN = float("nan")
MAX_LENGTH = 5000
MAX_STATEMENTS = 800
MAX_CALL_DEPTH = 32
DYNAMIC_BUILTINS = {
    "strategy.position_size",
    "strategy.position_avg_price",
    "strategy.opentrades",
}
SERIES_FUNCTIONS = {
    name for name, function in FUNCTIONS.items() if function.category == "ta"
} | {"math.sum", "fixnan"}
ZERO_OK = {"offset", "occurrence", "leftbars", "rightbars", "precision", "length_0"}
TIMEFRAMES = {
    "1m": "1",
    "2m": "2",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1d": "D",
    "1W": "W",
    "1M": "M",
}
# Inputs become backtesting.py strategy attributes, so they must not shadow these.
RESERVED = {
    "data",
    "position",
    "orders",
    "trades",
    "closed_trades",
    "equity",
    "buy",
    "sell",
    "init",
    "next",
    "I",
    "script",
    "param_names",
    "param_grid",
    "constraint",
    "result",
}
PLOT_STYLES = {
    "plot.style_line": "line",
    "plot.style_linebr": "line",
    "plot.style_stepline": "step",
    "plot.style_stepline_diamond": "step",
    "plot.style_histogram": "histogram",
    "plot.style_columns": "columns",
    "plot.style_circles": "circles",
    "plot.style_cross": "circles",
    "plot.style_area": "area",
    "plot.style_areabr": "area",
}
INPUT_KINDS = {
    "input.int": "int",
    "input.float": "float",
    "input.price": "float",
    "input.bool": "bool",
    "input.string": "string",
    "input.text_area": "string",
    "input.timeframe": "string",
    "input.session": "string",
    "input.symbol": "string",
    "input.source": "source",
    "input.color": "color",
}


# --- Values ----------------------------------------------------------------


def _describe(value: Any) -> str:
    if isinstance(value, str):
        return "metin"
    if isinstance(value, tuple):
        return "çoklu değer"
    if isinstance(value, np.ndarray) and value.dtype == object:
        return "metin serisi"
    return type(value).__name__


def is_na(value: Any) -> bool:
    """Return whether a scalar is ``na``."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def to_float(value: Any) -> Any:
    """Convert a value to a float series or number; booleans become 1/0."""
    if isinstance(value, np.ndarray):
        if value.dtype == bool:
            return value.astype(float)
        if value.dtype == object:
            raise ScriptError("Sayı bekleniyordu, metin serisi bulundu.")
        return value.astype(float, copy=False)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        return value
    if value is None:
        return NAN
    raise ScriptError(f"Sayı bekleniyordu, {_describe(value)} bulundu.")


def to_bool(value: Any) -> Any:
    """Convert a value to a boolean series or bool; ``na`` is false."""
    if isinstance(value, np.ndarray):
        if value.dtype == bool:
            return value
        if value.dtype == object:
            return np.array([not is_na(item) and bool(item) for item in value])
        return (value != 0) & ~np.isnan(value)
    if isinstance(value, float):
        return not math.isnan(value) and value != 0
    if isinstance(value, tuple):
        raise ScriptError("Çoklu değer koşul olarak kullanılamaz.")
    return value is not None and bool(value)


def broadcast(value: Any, n: int) -> np.ndarray:
    """Return a float series of length ``n``."""
    value = to_float(value)
    if isinstance(value, np.ndarray):
        return value
    return np.full(n, float(value))


def _clean(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    values[~np.isfinite(values)] = NAN
    return values


def _scalar_arith(op: str, x: Any, y: Any) -> Any:
    if is_na(x) or is_na(y):
        return NAN
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if y == 0:
        return NAN
    if op == "/":
        return x / y
    return math.fmod(x, y)


ARITH = {
    "+": np.add,
    "-": np.subtract,
    "*": np.multiply,
    "/": np.divide,
    "%": np.fmod,
}
COMPARE = {
    "<": np.less,
    ">": np.greater,
    "<=": np.less_equal,
    ">=": np.greater_equal,
    "==": np.equal,
    "!=": np.not_equal,
}


def arith(op: str, left: Any, right: Any) -> Any:
    """Apply an arithmetic operator with Pine's ``na`` semantics."""
    if isinstance(left, str) or isinstance(right, str):
        if op == "+" and isinstance(left, str) and isinstance(right, str):
            return left + right
        raise ScriptError("Metinlerle aritmetik işlem yapılamaz.")
    x, y = to_float(left), to_float(right)
    if not isinstance(x, np.ndarray) and not isinstance(y, np.ndarray):
        return _scalar_arith(op, x, y)
    with np.errstate(all="ignore"):
        out = ARITH[op](x, y)
    return _clean(out) if op in ("/", "%") else np.asarray(out, dtype=float)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, np.ndarray) and value.dtype == object
    )


def compare(op: str, left: Any, right: Any) -> Any:
    """Compare values; comparisons with ``na`` are false."""
    if _is_text(left) or _is_text(right):
        if op not in ("==", "!="):
            raise ScriptError("Metinler yalnızca == ve != ile karşılaştırılabilir.")
        if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
            equal = np.equal(
                np.asarray(left, dtype=object), np.asarray(right, dtype=object)
            )
            equal = np.asarray(equal, dtype=bool)
        else:
            equal = left == right
        return (
            equal
            if op == "=="
            else ~equal
            if isinstance(equal, np.ndarray)
            else not equal
        )
    x, y = to_float(left), to_float(right)
    if not isinstance(x, np.ndarray) and not isinstance(y, np.ndarray):
        if is_na(x) or is_na(y):
            return False
        return bool(COMPARE[op](x, y))
    with np.errstate(invalid="ignore"):
        out = COMPARE[op](x, y)
    if op == "!=":
        out &= ~np.isnan(np.broadcast_to(x, out.shape)) & ~np.isnan(
            np.broadcast_to(y, out.shape)
        )
    return out


def _is_boolean(value: Any) -> bool:
    return isinstance(value, (bool, np.bool_)) or (
        isinstance(value, np.ndarray) and value.dtype == bool
    )


def choose(cond: Any, then: Any, other: Any) -> Any:
    """Vectorized ``cond ? then : other``."""
    if _is_text(then) or _is_text(other):
        return np.where(
            cond, np.asarray(then, dtype=object), np.asarray(other, dtype=object)
        )
    if _is_boolean(then) and _is_boolean(other):
        return np.where(cond, then, other)
    return np.where(cond, to_float(then), to_float(other)).astype(float)


def shift_any(values: np.ndarray, bars: Any) -> np.ndarray:
    """Return a series ``bars`` bars ago; ``bars`` may itself be a series."""
    if isinstance(bars, np.ndarray):
        offsets = np.nan_to_num(to_float(bars), nan=-1).astype(int)
        positions = np.arange(len(values)) - offsets
        valid = (positions >= 0) & (offsets >= 0) & (positions < len(values))
        if values.dtype == bool:
            out = np.zeros(len(values), dtype=bool)
        elif values.dtype == object:
            out = np.full(len(values), None, dtype=object)
        else:
            out = np.full(len(values), NAN)
        out[valid] = values[positions[valid]]
        return out
    if values.dtype == object:
        out = np.full(len(values), None, dtype=object)
        if bars < len(values):
            out[bars:] = values[: len(values) - bars]
        return out
    return ta.shift(values, bars)


def _history_offset(value: Any, node: Node) -> int | np.ndarray:
    if isinstance(value, np.ndarray):
        return value
    if is_na(value):
        raise ScriptError("Geçmiş indeksi na olamaz.", node.line, node.col)
    offset = int(value)
    if offset < 0:
        raise ScriptError(
            "Geçmiş indeksi negatif olamaz; gelecekteki barlar görülemez.",
            node.line,
            node.col,
        )
    if offset > MAX_LENGTH:
        raise ScriptError(f"Geçmiş indeksi en fazla {MAX_LENGTH}.", node.line, node.col)
    return offset


def _color_name(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("#") else None


def slugify(text: str) -> str:
    """Turn a title into a Python identifier, transliterating Turkish letters."""
    text = text.translate(str.maketrans("ıİşŞğĞçÇöÖüÜ", "iIsSgGcCoOuU"))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")
    if not text or text[0].isdigit():
        text = f"p_{text}"
    return text


# --- Results ---------------------------------------------------------------


@dataclass
class InputSpec:
    """A script parameter declared with ``input*()``."""

    name: str
    kind: str
    default: Any
    title: str
    minval: float | None = None
    maxval: float | None = None
    step: float | None = None
    options: list | None = None

    def to_dict(self) -> dict:
        """Return the spec as a JSON-friendly dict."""
        return asdict(self)

    def coerce(self, value: Any) -> Any:
        """Validate and convert a value given for this input."""
        label = self.title or self.name
        try:
            if self.kind == "int":
                value = round(float(value))
            elif self.kind in ("float",):
                value = float(value)
            elif self.kind == "bool":
                if isinstance(value, str):
                    value = value.strip().lower() in ("true", "1", "evet", "yes")
                value = bool(value)
            elif self.kind in ("string", "source", "color"):
                value = str(value)
        except (TypeError, ValueError):
            raise ScriptError(f"'{label}' için geçersiz değer: {value!r}") from None
        if self.kind in ("int", "float"):
            if not math.isfinite(value):
                raise ScriptError(f"'{label}' sonlu bir sayı olmalı.")
            if self.minval is not None and value < self.minval:
                raise ScriptError(f"'{label}' en az {self.minval:g} olmalı.")
            if self.maxval is not None and value > self.maxval:
                raise ScriptError(f"'{label}' en fazla {self.maxval:g} olmalı.")
        if self.kind == "source" and value not in SOURCES:
            raise ScriptError(f"'{label}' şunlardan biri olmalı: {', '.join(SOURCES)}.")
        if self.options and value not in self.options:
            choices = ", ".join(str(option) for option in self.options)
            raise ScriptError(f"'{label}' şunlardan biri olmalı: {choices}.")
        return value


@dataclass
class Declaration:
    """What ``indicator()`` or ``strategy()`` declared."""

    kind: str = "indicator"
    title: str = "Script"
    shorttitle: str | None = None
    overlay: bool = False
    qty_type: str | None = None
    qty_value: float | None = None

    def to_dict(self) -> dict:
        """Return the declaration as a JSON-friendly dict."""
        return asdict(self)


@dataclass
class Plot:
    """A series drawn with ``plot()``."""

    title: str
    values: np.ndarray
    color: str | None = None
    style: str = "line"
    linewidth: float = 1
    overlay: bool = False
    colors: list | None = None


@dataclass
class Shape:
    """Markers from ``plotshape()``/``plotchar()``."""

    title: str
    mask: np.ndarray
    style: str = "circle"
    location: str = "abovebar"
    color: str | None = None
    text: str | None = None


@dataclass
class HLine:
    """A horizontal line from ``hline()``."""

    price: float
    title: str
    color: str | None = None


@dataclass
class Alert:
    """An ``alertcondition()`` or ``alert()`` and the bars it fired on."""

    title: str
    message: str
    mask: np.ndarray


@dataclass
class ScriptResult:
    """Everything a script produced over a frame of bars."""

    declaration: Declaration
    inputs: dict[str, Any]
    index: pd.Index
    plots: list[Plot] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)
    hlines: list[HLine] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    entries: np.ndarray | None = None
    exits: np.ndarray | None = None
    entry_qty: np.ndarray | None = None
    entry_limit: np.ndarray | None = None
    entry_stop: np.ndarray | None = None
    stops: np.ndarray | None = None
    limits: np.ndarray | None = None
    loss_ticks: np.ndarray | None = None
    profit_ticks: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_strategy(self) -> bool:
        """Whether the script declared ``strategy()``."""
        return self.declaration.kind == "strategy"


# --- Bars --------------------------------------------------------------------


class Bars:
    """Price series and bar facts a script can read."""

    def __init__(self, data: pd.DataFrame, symbol: str | None, interval: str):
        missing = {"Open", "High", "Low", "Close"} - set(data.columns)
        if missing:
            raise ScriptError(f"Veride eksik sütunlar: {', '.join(sorted(missing))}.")
        self.index = data.index
        self.n = len(data)
        self.open = data["Open"].to_numpy(dtype=float)
        self.high = data["High"].to_numpy(dtype=float)
        self.low = data["Low"].to_numpy(dtype=float)
        self.close = data["Close"].to_numpy(dtype=float)
        self.volume = (
            data["Volume"].to_numpy(dtype=float)
            if "Volume" in data
            else np.zeros(self.n)
        )
        self.symbol = (symbol or "SEMBOL").upper()
        self.interval = interval
        self._cache: dict[str, Any] = {}

    @property
    def local_index(self) -> pd.DatetimeIndex:
        """Bar times in Istanbul time."""
        index = pd.DatetimeIndex(self.index)
        if index.tz is None:
            return index.tz_localize(
                "Europe/Istanbul", ambiguous="NaT", nonexistent="NaT"
            )
        return index.tz_convert("Europe/Istanbul")

    @property
    def sessions(self) -> np.ndarray:
        """A key per trading day, to restart session-based values like VWAP."""
        if "sessions" not in self._cache:
            self._cache["sessions"] = pd.DatetimeIndex(self.index).normalize().asi8
        return self._cache["sessions"]

    def series(self, name: str) -> np.ndarray:
        """Return a price series by name."""
        if name in ("open", "high", "low", "close", "volume"):
            return getattr(self, name)
        if name == "hl2":
            return (self.high + self.low) / 2
        if name == "hlc3":
            return (self.high + self.low + self.close) / 3
        if name == "ohlc4":
            return (self.open + self.high + self.low + self.close) / 4
        if name == "hlcc4":
            return (self.high + self.low + 2 * self.close) / 4
        raise KeyError(name)

    def builtin(self, name: str) -> Any:
        """Return a built-in variable, computing it once."""
        if name in self._cache:
            return self._cache[name]
        value = self._compute(name)
        self._cache[name] = value
        return value

    def _compute(self, name: str) -> Any:
        if name in SOURCES:
            return self.series(name)
        local = self.local_index
        timeframe = TIMEFRAMES.get(self.interval, "D")
        values = {
            "bar_index": lambda: np.arange(self.n, dtype=float),
            "last_bar_index": lambda: self.n - 1,
            "time": lambda: (local.asi8 // 1_000_000).astype(float),
            "time_close": lambda: (local.asi8 // 1_000_000).astype(float),
            "year": lambda: local.year.to_numpy(dtype=float),
            "month": lambda: local.month.to_numpy(dtype=float),
            "weekofyear": lambda: local.isocalendar().week.to_numpy(dtype=float),
            "dayofmonth": lambda: local.day.to_numpy(dtype=float),
            "dayofweek": lambda: ((local.dayofweek.to_numpy() + 1) % 7 + 1).astype(
                float
            ),
            "hour": lambda: local.hour.to_numpy(dtype=float),
            "minute": lambda: local.minute.to_numpy(dtype=float),
            "ta.tr": lambda: ta.tr(self.high, self.low, self.close),
            "ta.obv": lambda: ta.obv(self.close, self.volume),
            "ta.vwap": lambda: ta.vwap(self.series("hlc3"), self.volume, self.sessions),
            "ta.accdist": lambda: ta.accdist(
                self.high, self.low, self.close, self.volume
            ),
            "syminfo.ticker": lambda: self.symbol,
            "syminfo.tickerid": lambda: f"BIST:{self.symbol}",
            "syminfo.root": lambda: self.symbol,
            "syminfo.prefix": lambda: "BIST",
            "syminfo.description": lambda: self.symbol,
            "syminfo.mintick": lambda: tick_size(self.close[-1]) if self.n else 0.01,
            "timeframe.period": lambda: timeframe,
            "timeframe.multiplier": lambda: (
                int(timeframe) if timeframe.isdigit() else 1
            ),
            "timeframe.isdaily": lambda: timeframe == "D",
            "timeframe.isweekly": lambda: timeframe == "W",
            "timeframe.ismonthly": lambda: timeframe == "M",
            "timeframe.isdwm": lambda: timeframe in ("D", "W", "M"),
            "timeframe.isintraday": timeframe.isdigit,
            "timeframe.isminutes": timeframe.isdigit,
            "barstate.isfirst": lambda: np.arange(self.n) == 0,
            "barstate.islast": lambda: np.arange(self.n) == self.n - 1,
            "barstate.isconfirmed": lambda: np.ones(self.n, dtype=bool),
            "barstate.ishistory": lambda: np.ones(self.n, dtype=bool),
            "barstate.isnew": lambda: np.ones(self.n, dtype=bool),
            "barstate.isrealtime": lambda: np.zeros(self.n, dtype=bool),
            "barstate.islastconfirmedhistory": lambda: np.arange(self.n) == self.n - 1,
        }
        if name not in values:
            raise KeyError(name)
        return values[name]()

    def prefix(self, length: int) -> Bars:
        """Return a view of the first ``length`` bars (for bar-by-bar calls)."""
        view = object.__new__(Bars)
        view.index = self.index[:length]
        view.n = length
        for name in ("open", "high", "low", "close", "volume"):
            setattr(view, name, getattr(self, name)[:length])
        view.symbol, view.interval = self.symbol, self.interval
        view._cache = {"sessions": self.sessions[:length]}
        return view


# --- Static analysis -----------------------------------------------------------


@dataclass
class _Item:
    statement: Node
    seq: int
    parents: tuple[If, ...]
    assigns: set[str]
    reads: set[str]
    history: set[str]


def _names(node: Node, functions: dict[str, set[str]]) -> tuple[set[str], set[str]]:
    """Return the names an expression reads now and the ones it reads from history."""
    reads: set[str] = set()
    history: set[str] = set()

    def visit(node: Node, past: bool) -> None:
        if isinstance(node, Name):
            (history if past else reads).add(node.id)
        elif isinstance(node, Index):
            literal_zero = isinstance(node.offset, Num) and node.offset.value == 0
            visit(node.target, past or not literal_zero)
            visit(node.offset, past)
        elif isinstance(node, Call):
            if node.func in functions:
                (history if past else reads).update(functions[node.func])
            for arg in (*node.args, *node.kwargs.values()):
                visit(arg, past)
        elif isinstance(node, Unary):
            visit(node.operand, past)
        elif isinstance(node, Binary):
            visit(node.left, past)
            visit(node.right, past)
        elif isinstance(node, Ternary):
            visit(node.cond, past)
            visit(node.then, past)
            visit(node.other, past)
        elif isinstance(node, TupleLit):
            for item in node.items:
                visit(item, past)

    visit(node, False)
    return reads, history


class Analysis:
    """Find the statements that must run bar by bar."""

    def __init__(self, program: list[Node], functions: dict[str, FuncDef]):
        self.free_names = {
            name: self._free_names(function) for name, function in functions.items()
        }
        self.items: list[_Item] = []
        self._linearize(program, ())
        last_assign: dict[str, int] = {}
        self.var_names: set[str] = set()
        for item in self.items:
            for name in item.assigns:
                last_assign[name] = item.seq
            statement = item.statement
            if isinstance(statement, Assign) and statement.mode != "decl":
                self.var_names.update(statement.targets)
        recurrent = {
            name
            for item in self.items
            for name in item.history
            if name in last_assign and item.seq <= last_assign[name]
        }
        self.dynamic_names = self.var_names | recurrent | set(DYNAMIC_BUILTINS)
        self.dynamic: set[Node] = set()
        self.dynamic_ifs: set[If] = set()
        self._propagate()
        self.contains_dynamic: set[If] = set()
        for item in self.items:
            if item.statement in self.dynamic:
                self.contains_dynamic.update(item.parents)
        self.assigned_dynamic = {
            name for item in self.items for name in item.assigns
        } & self.dynamic_names
        self.uses_position = any(
            (item.reads | item.history) & DYNAMIC_BUILTINS for item in self.items
        )
        self._expr_cache: dict[Node, bool] = {}

    def _free_names(self, function: FuncDef) -> set[str]:
        local = set(function.params)
        names: set[str] = set()
        for statement in function.body:
            for node in walk(statement):
                if isinstance(node, Name) and node.id not in local:
                    names.add(node.id)
            if isinstance(statement, Assign):
                local.update(statement.targets)
        return names

    def _linearize(self, statements: list[Node], parents: tuple[If, ...]) -> None:
        for statement in statements:
            assigns: set[str] = set()
            if isinstance(statement, If):
                reads, history = _names(statement.cond, self.free_names)
            elif isinstance(statement, Assign):
                assigns = set(statement.targets)
                reads, history = _names(statement.value, self.free_names)
            elif isinstance(statement, Reassign):
                assigns = {statement.target}
                reads, history = _names(statement.value, self.free_names)
                if statement.op != ":=":
                    reads.add(statement.target)
            elif isinstance(statement, ExprStmt):
                reads, history = _names(statement.expr, self.free_names)
            else:
                continue
            self.items.append(
                _Item(statement, len(self.items), parents, assigns, reads, history)
            )
            if isinstance(statement, If):
                self._linearize(statement.body, (*parents, statement))
                self._linearize(statement.orelse, (*parents, statement))

    def _propagate(self) -> None:
        changed = True
        while changed:
            changed = False
            for item in self.items:
                statement = item.statement
                in_dynamic_block = any(p in self.dynamic_ifs for p in item.parents)
                touches = (
                    item.reads | item.history | item.assigns
                ) & self.dynamic_names
                if not (in_dynamic_block or touches) or statement in self.dynamic:
                    continue
                if isinstance(statement, If) and not (
                    in_dynamic_block or (item.reads | item.history) & self.dynamic_names
                ):
                    continue
                self.dynamic.add(statement)
                changed = True
                if isinstance(statement, If):
                    self.dynamic_ifs.add(statement)
                new = item.assigns - self.dynamic_names
                if new:
                    self.dynamic_names |= new

    def is_dynamic(self, node: Node) -> bool:
        """Return whether an expression depends on bar-by-bar state."""
        cached = self._expr_cache.get(node)
        if cached is None:
            reads, history = _names(node, self.free_names)
            cached = bool((reads | history) & self.dynamic_names)
            self._expr_cache[node] = cached
        return cached


# --- Script ----------------------------------------------------------------------


def _count_statements(statements: list[Node]) -> int:
    total = 0
    for statement in statements:
        total += 1
        if isinstance(statement, If):
            total += _count_statements(statement.body) + _count_statements(
                statement.orelse
            )
        elif isinstance(statement, FuncDef):
            total += _count_statements(statement.body)
    return total


def const_value(node: Node) -> Any:
    """Evaluate a constant expression such as an input default."""
    if isinstance(node, (Num, Str, Bool, Color)):
        return node.value
    if isinstance(node, Na):
        return None
    if isinstance(node, Unary) and node.op in "-+":
        value = const_value(node.operand)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return -value if node.op == "-" else value
    if isinstance(node, TupleLit):
        return [const_value(item) for item in node.items]
    if isinstance(node, Name):
        if node.id in SOURCES:
            return node.id
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        if node.id.split(".")[0] in ENUM_NAMESPACES:
            return node.id
    if isinstance(node, Call) and node.func in ("color.new", "color.rgb"):
        return None
    raise ScriptError("Burada sabit bir değer bekleniyordu.", node.line, node.col)


def _bind_nodes(function: Function, call: Call) -> dict[str, Node | None]:
    """Map a call's argument nodes to parameter names (no evaluation)."""
    params = function.params
    bound: dict[str, Node | None] = {}
    if len(call.args) > len(params):
        raise ScriptError(
            f"{function.name}: çok fazla argüman. Kullanım: {function.signature()}",
            call.line,
            call.col,
        )
    for param, arg in zip(params, call.args):
        bound[param.name] = arg
    names = {param.name for param in params}
    for key, arg in call.kwargs.items():
        if key in names:
            bound[key] = arg
    return bound


class Script:
    """A parsed script, ready to run on any symbol."""

    def __init__(self, source: str, name: str | None = None):
        self.source = source
        self.name = name
        self.program = parse(source)
        if _count_statements(self.program) > MAX_STATEMENTS:
            raise ScriptError(f"Script en fazla {MAX_STATEMENTS} deyim içerebilir.")
        self.functions: dict[str, FuncDef] = {}
        for statement in self.program:
            if isinstance(statement, FuncDef):
                if statement.name in FUNCTIONS:
                    raise ScriptError(
                        f"'{statement.name}' yerleşik bir fonksiyonun adı.",
                        statement.line,
                        statement.col,
                    )
                self.functions[statement.name] = statement
        self._check_calls()
        self.declaration = self._declaration()
        self.input_names: dict[Call, str] = {}
        self.inputs = self._inputs()
        self.analysis = Analysis(self.program, self.functions)

    # Compile-time checks -------------------------------------------------------

    def _check_calls(self) -> None:
        known = set(FUNCTIONS) | set(self.functions)
        for node in (n for statement in self.program for n in walk(statement)):
            if not isinstance(node, Call):
                continue
            namespace = node.func.split(".")[0] if "." in node.func else None
            if namespace in UNSUPPORTED_NAMESPACES:
                raise ScriptError(
                    UNSUPPORTED_NAMESPACES[namespace], node.line, node.col
                )
            if node.func in known or namespace in DRAWING_NAMESPACES:
                continue
            close = difflib.get_close_matches(node.func, known, n=1)
            hint = f" Bunu mu demek istediniz: {close[0]}?" if close else ""
            raise ScriptError(
                f"Bilinmeyen fonksiyon: {node.func}.{hint}", node.line, node.col
            )
        for statement in self.program:
            if isinstance(statement, FuncDef):
                for node in (n for s in statement.body for n in walk(s)):
                    if isinstance(node, Call) and (
                        node.func in EFFECTS or node.func.startswith("input")
                    ):
                        raise ScriptError(
                            f"{node.func} fonksiyon içinde kullanılamaz.",
                            node.line,
                            node.col,
                        )
                for inner in statement.body:
                    if isinstance(inner, If):
                        raise ScriptError(
                            "Fonksiyon içinde 'if' desteklenmiyor; '?:' kullanın.",
                            inner.line,
                            inner.col,
                        )

    def _declaration(self) -> Declaration:
        found = [
            statement.expr
            for statement in self.program
            if isinstance(statement, ExprStmt)
            and isinstance(statement.expr, Call)
            and statement.expr.func in DECLARATIONS
        ]
        if len(found) > 1:
            raise ScriptError(
                "Script yalnızca bir indicator() ya da strategy() bildirimi içerebilir.",
                found[1].line,
                found[1].col,
            )
        default_title = self.name or "Script"
        if not found:
            return Declaration(title=default_title)
        call = found[0]
        args = {
            key: const_value(node)
            for key, node in _bind_nodes(FUNCTIONS[call.func], call).items()
            if node is not None
        }
        declaration = Declaration(
            kind="strategy" if call.func == "strategy" else "indicator",
            title=str(args.get("title") or default_title),
            shorttitle=args.get("shorttitle"),
            overlay=bool(args.get("overlay", False)),
        )
        if declaration.kind == "strategy":
            qty_type = args.get("default_qty_type")
            if qty_type is not None and qty_type not in (
                "fixed",
                "cash",
                "percent_of_equity",
            ):
                raise ScriptError(
                    "default_qty_type strategy.fixed, strategy.cash ya da"
                    " strategy.percent_of_equity olmalı.",
                    call.line,
                    call.col,
                )
            qty_value = args.get("default_qty_value")
            declaration.qty_type = qty_type
            declaration.qty_value = float(qty_value) if qty_value is not None else None
        return declaration

    def _inputs(self) -> list[InputSpec]:
        targets: dict[Call, str] = {}
        for statement in (n for s in self.program for n in walk(s)):
            if (
                isinstance(statement, Assign)
                and len(statement.targets) == 1
                and isinstance(statement.value, Call)
            ):
                targets[statement.value] = statement.targets[0]
        specs: list[InputSpec] = []
        used: set[str] = set()
        for node in (n for s in self.program for n in walk(s)):
            if not (isinstance(node, Call) and FUNCTIONS.get(node.func)) or (
                FUNCTIONS[node.func].category != "input"
            ):
                continue
            spec = self._input_spec(node, targets.get(node), len(specs))
            name = spec.name
            suffix = 2
            while name in used or name in RESERVED or name.startswith("_"):
                name = f"{spec.name}_{suffix}"
                suffix += 1
            spec.name = name
            used.add(name)
            self.input_names[node] = name
            specs.append(spec)
        return specs

    def _input_spec(self, call: Call, target: str | None, position: int) -> InputSpec:
        function = FUNCTIONS[call.func]
        nodes = _bind_nodes(function, call)
        if nodes.get("defval") is None:
            raise ScriptError(
                f"{call.func}: varsayılan değer (defval) gerekli.", call.line, call.col
            )
        values = {
            key: const_value(node) for key, node in nodes.items() if node is not None
        }
        default = values["defval"]
        kind = INPUT_KINDS.get(call.func)
        if kind is None:
            if isinstance(default, bool):
                kind = "bool"
            elif isinstance(default, int):
                kind = "int"
            elif isinstance(default, float):
                kind = "float"
            elif default in SOURCES:
                kind = "source"
            else:
                kind = "string"
        title = values.get("title")
        name = target or slugify(str(title or f"input{position + 1}"))
        spec = InputSpec(
            name=name,
            kind=kind,
            default=default,
            title=str(title or target or name),
            minval=values.get("minval"),
            maxval=values.get("maxval"),
            step=values.get("step"),
            options=values.get("options"),
        )
        try:
            spec.default = spec.coerce(default)
        except ScriptError as error:
            raise error.located(call.line, call.col) from None
        return spec

    # Running -------------------------------------------------------------------

    def resolve_inputs(self, values: dict[str, Any] | None) -> dict[str, Any]:
        """Return every input's value, using defaults for the ones not given."""
        values = dict(values or {})
        names = {spec.name for spec in self.inputs}
        unknown = set(values) - names
        if unknown:
            listed = ", ".join(sorted(names)) or "yok"
            raise ScriptError(
                f"Bilinmeyen parametre: {', '.join(sorted(unknown))}. Parametreler: {listed}."
            )
        return {
            spec.name: spec.coerce(values[spec.name])
            if spec.name in values
            else spec.default
            for spec in self.inputs
        }

    def run(
        self,
        data: pd.DataFrame,
        inputs: dict[str, Any] | None = None,
        *,
        symbol: str | None = None,
        interval: str | None = None,
    ) -> ScriptResult:
        """Run the script over ``data`` (an OHLCV frame) and collect its output.

        ``interval`` sets ``timeframe.*``; it is inferred from the bar spacing
        when not given.
        """
        interval = interval or infer_interval(data.index)
        return _Run(self, data, self.resolve_inputs(inputs), symbol, interval).execute()

    def describe(self) -> dict:
        """Return the script's declaration and inputs for the UI and the AI."""
        return {
            "name": self.name,
            **self.declaration.to_dict(),
            "inputs": [spec.to_dict() for spec in self.inputs],
            "stateful": bool(self.analysis.dynamic),
        }


def infer_interval(index: pd.Index) -> str:
    """Guess the bar size of a time index from its typical spacing."""
    if len(index) < 2 or not isinstance(index, pd.DatetimeIndex):
        return "1d"
    step = pd.Series(index).diff().median()
    if step >= pd.Timedelta(days=25):
        return "1M"
    if step >= pd.Timedelta(days=5):
        return "1W"
    if step >= pd.Timedelta(hours=20):
        return "1d"
    minutes = max(int(step / pd.Timedelta(minutes=1)), 1)
    for interval, size in (("1h", 60), ("30m", 30), ("15m", 15), ("5m", 5), ("2m", 2)):
        if minutes >= size:
            return interval
    return "1m"


def compile_script(source: str, name: str | None = None) -> Script:
    """Parse and check a script."""
    return Script(source, name)


# --- Interpreter -------------------------------------------------------------------


class _PositionSim:
    """Approximate the position from the script's own signals, for scripts that
    read ``strategy.position_size``. Orders fill at the next bar's open and the
    latest ``strategy.exit`` levels close the position when touched.
    """

    def __init__(self, run: _Run):
        self.run = run
        self.size = 0.0
        self.average = NAN
        self.pending: str | None = None
        self.stop = NAN
        self.limit = NAN

    def start(self, i: int) -> None:
        bars = self.run.bars
        if self.pending == "entry" and not self.size:
            self.size, self.average = 1.0, bars.open[i]
        elif self.pending == "exit":
            self.size, self.average = 0.0, NAN
            self.stop = self.limit = NAN
        self.pending = None
        stopped = not math.isnan(self.stop) and bars.low[i] <= self.stop
        limited = not math.isnan(self.limit) and bars.high[i] >= self.limit
        if self.size and (stopped or limited):
            self.size, self.average, self.stop, self.limit = 0.0, NAN, NAN, NAN

    def end(self, i: int) -> None:
        run = self.run
        if not math.isnan(run.stops[i]):
            self.stop = run.stops[i]
        if not math.isnan(run.limits[i]):
            self.limit = run.limits[i]
        if self.size and not math.isnan(self.average):
            tick = tick_size(self.average)
            if not math.isnan(run.loss_ticks[i]):
                self.stop = self.average - run.loss_ticks[i] * tick
            if not math.isnan(run.profit_ticks[i]):
                self.limit = self.average + run.profit_ticks[i] * tick
        if self.size and run.exits[i]:
            self.pending = "exit"
        elif not self.size and run.entries[i]:
            self.pending = "entry"

    def value(self, name: str) -> float:
        if name == "strategy.position_avg_price":
            return self.average
        return self.size


class _Run:
    """One execution of a script over a frame of bars."""

    def __init__(self, script, data, inputs, symbol, interval):
        self.script = script
        self.analysis: Analysis = script.analysis
        self.bars = Bars(data, symbol, interval)
        self.n = self.bars.n
        self.inputs = inputs
        self.env: dict[str, Any] = {}
        self.depth = 0
        self.warnings: list[str] = []
        self.pre: dict[Node, Any] = {}
        self.conds: dict[If, np.ndarray] = {}
        self.plots: list[Plot] = []
        self.shapes: list[Shape] = []
        self.hlines: list[HLine] = []
        self.alerts: list[Alert] = []
        self.handles: dict[Call, Any] = {}
        n = self.n
        self.entries = np.zeros(n, dtype=bool)
        self.exits = np.zeros(n, dtype=bool)
        self.entry_qty = np.full(n, NAN)
        self.entry_limit = np.full(n, NAN)
        self.entry_stop = np.full(n, NAN)
        self.stops = np.full(n, NAN)
        self.limits = np.full(n, NAN)
        self.loss_ticks = np.full(n, NAN)
        self.profit_ticks = np.full(n, NAN)
        # Bar-by-bar state.
        self.i = 0
        self.state: dict[str, Any] = {}
        self.history: dict[str, list] = {}
        self.initialized: set[str] = set()
        self.expr_history: dict[tuple, list] = {}
        self.sites: dict[tuple, dict[str, np.ndarray]] = {}
        self.stack: tuple = ()
        self.i_mode = False
        self.sim = _PositionSim(self)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def execute(self) -> ScriptResult:
        self.block(self.script.program, None)
        if self.analysis.dynamic:
            self.run_bar_by_bar()
        declaration = self.script.declaration
        is_strategy = declaration.kind == "strategy"
        return ScriptResult(
            declaration=declaration,
            inputs=self.inputs,
            index=self.bars.index,
            plots=self.plots,
            shapes=self.shapes,
            hlines=self.hlines,
            alerts=self.alerts,
            entries=self.entries if is_strategy else None,
            exits=self.exits if is_strategy else None,
            entry_qty=self.entry_qty,
            entry_limit=self.entry_limit,
            entry_stop=self.entry_stop,
            stops=self.stops,
            limits=self.limits,
            loss_ticks=self.loss_ticks,
            profit_ticks=self.profit_ticks,
            warnings=self.warnings,
        )

    # Phase one: vectorized ---------------------------------------------------------

    def block(self, statements: list[Node], mask: np.ndarray | None) -> None:
        for statement in statements:
            try:
                self.statement(statement, mask)
            except ScriptError as error:
                raise error.located(statement.line, statement.col) from None
            except (ArithmeticError, ValueError, TypeError, IndexError) as error:
                raise ScriptError(
                    f"Çalışma hatası: {error}", statement.line, statement.col
                ) from None

    def statement(self, statement: Node, mask: np.ndarray | None) -> None:
        if isinstance(statement, FuncDef):
            return
        if statement in self.analysis.dynamic:
            self.prepare(statement)
            return
        if isinstance(statement, If):
            cond = to_bool(self.eval(statement.cond))
            cond = np.broadcast_to(cond, (self.n,)).copy()
            self.conds[statement] = cond
            inner = cond if mask is None else cond & mask
            self.block(statement.body, inner)
            if statement.orelse:
                other = ~cond if mask is None else ~cond & mask
                self.block(statement.orelse, other)
        elif isinstance(statement, Assign):
            self.assign(statement, mask)
        elif isinstance(statement, Reassign):
            if statement.target not in self.env:
                raise ScriptError(
                    f"'{statement.target}' önce '=' ile tanımlanmalı.",
                    statement.line,
                    statement.col,
                )
            value = self.eval(statement.value)
            current = self.env[statement.target]
            if statement.op != ":=":
                value = arith(statement.op[0], current, value)
            self.env[statement.target] = self.masked(value, mask, current)
        elif isinstance(statement, ExprStmt):
            expr = statement.expr
            if isinstance(expr, Call) and expr.func in DECLARATIONS:
                return
            if isinstance(expr, Call) and expr.func in EFFECTS:
                self.effect(expr, mask)
            elif (
                isinstance(expr, Call) and expr.func.split(".")[0] in DRAWING_NAMESPACES
            ):
                self.drawing(expr)
            else:
                self.eval(expr)

    def assign(self, statement: Assign, mask: np.ndarray | None) -> None:
        value_node = statement.value
        if isinstance(value_node, Call) and value_node.func in EFFECTS:
            value = self.effect(value_node, mask)
        else:
            value = self.eval(value_node)
        if len(statement.targets) > 1:
            if not isinstance(value, tuple) or len(value) != len(statement.targets):
                count = len(value) if isinstance(value, tuple) else 1
                raise ScriptError(
                    f"{len(statement.targets)} değişkene {count} değer atanamaz.",
                    statement.line,
                    statement.col,
                )
            for target, item in zip(statement.targets, value):
                self.env[target] = self.masked(item, mask, None)
        else:
            if isinstance(value, tuple):
                raise ScriptError(
                    f"Bu fonksiyon {len(value)} değer döndürür; "
                    "[a, b, ...] = ... biçiminde atayın.",
                    statement.line,
                    statement.col,
                )
            self.env[statement.targets[0]] = self.masked(value, mask, None)

    def masked(self, value: Any, mask: np.ndarray | None, current: Any) -> Any:
        """Apply an assignment only on the bars where ``mask`` holds."""
        if mask is None or isinstance(value, str):
            return value
        if current is None:
            if isinstance(value, (bool, np.bool_)) or (
                isinstance(value, np.ndarray) and value.dtype == bool
            ):
                current = False
            elif _is_text(value):
                current = None
            else:
                current = NAN
        return choose(mask, value, current)

    def eval(self, node: Node, scope: dict | None = None) -> Any:
        if isinstance(node, (Num, Str, Bool, Color)):
            return node.value
        if isinstance(node, Na):
            return NAN
        if isinstance(node, Name):
            return self.lookup(node, scope)
        if isinstance(node, Index):
            target = self.eval(node.target, scope)
            offset = _history_offset(self.eval(node.offset, scope), node)
            if isinstance(target, np.ndarray):
                return shift_any(target, offset)
            if isinstance(target, tuple):
                raise ScriptError(
                    "Çoklu değerin geçmişi alınamaz.", node.line, node.col
                )
            return target
        if isinstance(node, Unary):
            value = self.eval(node.operand, scope)
            if node.op == "not":
                value = to_bool(value)
                return ~value if isinstance(value, np.ndarray) else not value
            value = to_float(value)
            return -value if node.op == "-" else value
        if isinstance(node, Binary):
            return self.binary(
                node, self.eval(node.left, scope), self.eval(node.right, scope)
            )
        if isinstance(node, Ternary):
            cond = to_bool(self.eval(node.cond, scope))
            if not isinstance(cond, np.ndarray):
                return self.eval(node.then if cond else node.other, scope)
            return choose(
                cond, self.eval(node.then, scope), self.eval(node.other, scope)
            )
        if isinstance(node, TupleLit):
            return tuple(self.eval(item, scope) for item in node.items)
        if isinstance(node, Call):
            return self.call(node, scope)
        raise ScriptError("Bu ifade burada kullanılamaz.", node.line, node.col)

    @staticmethod
    def binary(node: Binary, left: Any, right: Any) -> Any:
        op = node.op
        try:
            if op in ("and", "or"):
                x, y = to_bool(left), to_bool(right)
                if isinstance(x, np.ndarray) or isinstance(y, np.ndarray):
                    return (x & y) if op == "and" else (x | y)
                return (x and y) if op == "and" else (x or y)
            if op in COMPARE:
                return compare(op, left, right)
            return arith(op, left, right)
        except ScriptError as error:
            raise error.located(node.line, node.col) from None

    def lookup(self, node: Name, scope: dict | None) -> Any:
        name = node.id
        if scope is not None and name in scope:
            return scope[name]
        if name in self.env:
            return self.env[name]
        try:
            return self.bars.builtin(name)
        except KeyError:
            pass
        if name in CONSTANTS:
            return CONSTANTS[name]
        namespace = name.split(".")[0]
        if namespace in ENUM_NAMESPACES and "." in name:
            return name
        if namespace in UNSUPPORTED_NAMESPACES:
            raise ScriptError(UNSUPPORTED_NAMESPACES[namespace], node.line, node.col)
        if name in DYNAMIC_BUILTINS:
            return self.sim.value(name)
        if name in self.script.functions or name in FUNCTIONS:
            raise ScriptError(
                f"'{name}' bir fonksiyon; parantezle çağırın: {name}(...)",
                node.line,
                node.col,
            )
        known = list(self.env) + list(SOURCES) + list(CONSTANTS)
        close = difflib.get_close_matches(name, known, n=1)
        hint = f" Bunu mu demek istediniz: {close[0]}?" if close else ""
        raise ScriptError(f"Tanımsız değişken: {name}.{hint}", node.line, node.col)

    def call(self, node: Call, scope: dict | None) -> Any:
        if node.func in self.script.functions:
            return self.call_user(node, scope, self.eval)
        function = FUNCTIONS.get(node.func)
        if function is None:
            return self.drawing(node)
        if function.category == "input":
            return self.input_value(node)
        if function.impl is None:
            raise ScriptError(
                f"{node.func} bir ifade içinde kullanılamaz; ayrı bir satırda çağırın.",
                node.line,
                node.col,
            )
        if function.varargs:
            if node.kwargs or len(node.args) < 2:
                raise ScriptError(
                    f"{node.func} en az iki konumsal argüman alır.", node.line, node.col
                )
            values = [self.eval(arg, scope) for arg in node.args]
            return function.impl(self.bars, values)
        args = self.bind(function, node, lambda arg: self.eval(arg, scope))
        try:
            return function.impl(self.bars, self.convert(function, args, node))
        except ScriptError as error:
            raise error.located(node.line, node.col) from None

    def drawing(self, node: Call) -> Any:
        self.warn(
            "Etiket, çizgi, kutu ve tablo gibi çizim nesneleri desteklenmiyor;"
            " bu çağrılar yok sayıldı."
        )
        return NAN

    def input_value(self, node: Call) -> Any:
        name = self.script.input_names.get(node)
        if name is None:
            raise ScriptError("input() burada kullanılamaz.", node.line, node.col)
        value = self.inputs[name]
        spec = next(spec for spec in self.script.inputs if spec.name == name)
        if spec.kind == "source":
            return self.bars.series(value)
        return value

    def bind(self, function: Function, node: Call, evaluate) -> dict[str, Any]:
        """Evaluate a call's arguments and match them to parameters."""
        params = function.params
        values: dict[str, Any] = {}
        required = [p for p in params if p.default is REQUIRED]
        given = len(node.args) + len(node.kwargs)
        if (
            function.default_source
            and given == len(required) - 1
            and params[0].name not in node.kwargs
        ):
            values[params[0].name] = self.bars.series(function.default_source)
            positional = params[1:]
        else:
            positional = params
        if len(node.args) > len(positional):
            raise ScriptError(
                f"{function.name}: çok fazla argüman. Kullanım: {function.signature()}",
                node.line,
                node.col,
            )
        for param, arg in zip(positional, node.args):
            values[param.name] = evaluate(arg)
        names = {param.name for param in params}
        for key, arg in node.kwargs.items():
            if key not in names:
                if function.strict:
                    raise ScriptError(
                        f"{function.name}: bilinmeyen argüman '{key}'."
                        f" Kullanım: {function.signature()}",
                        arg.line,
                        arg.col,
                    )
                continue
            if key in values:
                raise ScriptError(
                    f"{function.name}: '{key}' argümanı iki kez verilmiş.",
                    arg.line,
                    arg.col,
                )
            values[key] = evaluate(arg)
        for param in params:
            if param.name not in values:
                if param.default is REQUIRED:
                    raise ScriptError(
                        f"{function.name}: '{param.name}' argümanı eksik."
                        f" Kullanım: {function.signature()}",
                        node.line,
                        node.col,
                    )
                values[param.name] = param.default
        return values

    def convert(self, function: Function, values: dict, node: Call) -> dict:
        """Convert bound arguments to the kinds the implementation expects."""
        out = {}
        for param in function.params:
            value = values[param.name]
            kind = param.kind
            if kind == "series":
                if isinstance(value, tuple):
                    raise ScriptError(
                        f"{function.name}: '{param.name}' tek bir seri olmalı.",
                        node.line,
                        node.col,
                    )
                value = broadcast(value, self.n) if self.n else np.zeros(0)
            elif kind == "cond":
                value = np.broadcast_to(to_bool(value), (self.n,))
            elif kind in ("int", "float"):
                value = self.constant(function, param.name, value, node, kind)
            elif kind == "bool":
                if isinstance(value, np.ndarray):
                    raise ScriptError(
                        f"{function.name}: '{param.name}' sabit true/false olmalı.",
                        node.line,
                        node.col,
                    )
                value = to_bool(value)
            out[param.name] = value
        return out

    def constant(self, function, name, value, node, kind):
        if isinstance(value, np.ndarray):
            finite = value[~np.isnan(to_float(value))] if value.size else value
            if finite.size and (finite == finite[-1]).all():
                value = finite[-1]
            else:
                raise ScriptError(
                    f"{function.name}: '{name}' sabit bir sayı olmalı (seri verilemez).",
                    node.line,
                    node.col,
                )
        value = to_float(value)
        if is_na(value):
            raise ScriptError(
                f"{function.name}: '{name}' na olamaz.", node.line, node.col
            )
        if kind == "float":
            return float(value)
        value = int(value)
        low = 0 if name in ZERO_OK else 1
        if not low <= value <= MAX_LENGTH:
            raise ScriptError(
                f"{function.name}: '{name}' {low} ile {MAX_LENGTH} arasında olmalı.",
                node.line,
                node.col,
            )
        return value

    def call_user(self, node: Call, scope: dict | None, evaluate) -> Any:
        definition = self.script.functions[node.func]
        if self.depth >= MAX_CALL_DEPTH:
            raise ScriptError(
                "Fonksiyonlar çok derin iç içe çağrılıyor (özyineleme desteklenmiyor).",
                node.line,
                node.col,
            )
        if len(node.args) > len(definition.params):
            raise ScriptError(
                f"{node.func} en fazla {len(definition.params)} argüman alır.",
                node.line,
                node.col,
            )
        local: dict[str, Any] = {}
        for param, arg in zip(definition.params, node.args):
            local[param] = evaluate(arg)
        for key, arg in node.kwargs.items():
            if key not in definition.params:
                raise ScriptError(
                    f"{node.func}: bilinmeyen argüman '{key}'.", arg.line, arg.col
                )
            local[key] = evaluate(arg)
        for param in definition.params:
            if param not in local:
                if param not in definition.defaults:
                    raise ScriptError(
                        f"{node.func}: '{param}' argümanı eksik.", node.line, node.col
                    )
                local[param] = evaluate(definition.defaults[param])
        self.depth += 1
        outer = self.stack
        self.stack = (*outer, node)
        try:
            return self.function_body(definition, local)
        finally:
            self.depth -= 1
            self.stack = outer

    def function_body(self, definition: FuncDef, local: dict) -> Any:
        dynamic = self.i_mode
        evaluate = (
            (lambda n: self.eval_at(n, local))
            if dynamic
            else (lambda n: self.eval(n, local))
        )
        for statement in definition.body[:-1]:
            if isinstance(statement, Assign):
                value = evaluate(statement.value)
                if len(statement.targets) > 1:
                    for target, item in zip(statement.targets, value):
                        local[target] = item
                else:
                    local[statement.targets[0]] = value
            elif isinstance(statement, Reassign):
                value = evaluate(statement.value)
                if statement.op != ":=":
                    value = arith(statement.op[0], local[statement.target], value)
                local[statement.target] = value
            elif isinstance(statement, ExprStmt):
                evaluate(statement.expr)
        return evaluate(definition.body[-1].expr)

    # Effects -------------------------------------------------------------------------

    def effect(self, node: Call, mask: np.ndarray | None) -> Any:
        """Run a plot, alert or strategy call over all bars."""
        function = FUNCTIONS[node.func]
        name = node.func
        args = self.bind(function, node, self.eval)
        if name.startswith("strategy."):
            return self.order(
                name, args, node, self.combine(mask, args.get("when", True))
            )
        if name == "alert":
            fired = np.ones(self.n, dtype=bool) if mask is None else mask.copy()
            self.alerts.append(Alert("alert()", str(args["message"]), fired))
            return None
        if mask is not None:
            raise ScriptError(
                f"{name} yerel kapsamda (if bloğu içinde) kullanılamaz.",
                node.line,
                node.col,
            )
        return self.drawn(name, args, node)

    def drawn(self, name: str, args: dict, node: Call) -> Any:
        declaration = self.script.declaration
        if name == "plot":
            series = args["series"]
            values = (
                broadcast(series, self.n)
                if not isinstance(series, np.ndarray) or series.dtype != object
                else np.full(self.n, NAN)
            )
            plot = Plot(
                title=str(args["title"] or f"Plot {len(self.plots) + 1}"),
                values=values,
                style=PLOT_STYLES.get(str(args["style"]), "line"),
                linewidth=float(to_float(args["linewidth"]) or 1),
                overlay=declaration.overlay or bool(args["force_overlay"]),
            )
            self.plot_color(plot, args["color"])
            self.plots.append(plot)
            self.handles[node] = plot
            return f"plot:{len(self.plots)}"
        if name in ("plotshape", "plotchar"):
            shape = Shape(
                title=str(args["title"] or name),
                mask=np.broadcast_to(to_bool(args["series"]), (self.n,)).copy(),
                style=str(args.get("style") or "shape.circle").split(".")[-1],
                location=str(args["location"]).split(".")[-1],
                color=_color_name(args["color"]),
                text=args["text"] if isinstance(args["text"], str) else None,
            )
            if name == "plotchar":
                shape.style = "char"
                shape.text = shape.text or str(args.get("char") or "★")
            self.shapes.append(shape)
            self.handles[node] = shape
            return f"shape:{len(self.shapes)}"
        if name == "hline":
            price = to_float(args["price"])
            if isinstance(price, np.ndarray):
                raise ScriptError("hline fiyatı sabit olmalı.", node.line, node.col)
            self.hlines.append(
                HLine(
                    float(price), str(args["title"] or ""), _color_name(args["color"])
                )
            )
            return f"hline:{len(self.hlines)}"
        if name == "alertcondition":
            fired = np.broadcast_to(to_bool(args["condition"]), (self.n,)).copy()
            title = str(args["title"] or f"Alarm {len(self.alerts) + 1}")
            self.alerts.append(Alert(title, str(args["message"] or title), fired))
            self.handles[node] = self.alerts[-1]
            return None
        if name in ("bgcolor", "barcolor", "fill"):
            return None
        return None

    def plot_color(self, plot: Plot, color: Any) -> None:
        if isinstance(color, np.ndarray) and color.dtype == object:
            colors = [_color_name(item) for item in color]
            plot.colors = colors
            plot.color = next((c for c in reversed(colors) if c), None)
        else:
            plot.color = _color_name(color)

    def combine(self, mask: np.ndarray | None, when: Any) -> np.ndarray:
        when = np.broadcast_to(to_bool(when), (self.n,))
        return when.copy() if mask is None else mask & when

    def order(self, name: str, args: dict, node: Call, fired: np.ndarray) -> None:
        if self.script.declaration.kind != "strategy":
            raise ScriptError(
                f"{name} için script strategy(...) ile bildirilmeli.",
                node.line,
                node.col,
            )
        if name == "strategy.entry":
            direction = args["direction"]
            if isinstance(direction, (bool, np.bool_)):
                direction = "long" if direction else "short"
            if direction == "short":
                self.warn(
                    "BIST'te açığa satış simüle edilmiyor: strategy.short girişleri"
                    " uzun pozisyonu kapatma olarak uygulandı."
                )
                self.exits |= fired
                return
            if direction != "long":
                raise ScriptError(
                    "Yön strategy.long ya da strategy.short olmalı.",
                    node.line,
                    node.col,
                )
            self.entries |= fired
            for key, target in (
                ("qty", self.entry_qty),
                ("limit", self.entry_limit),
                ("stop", self.entry_stop),
            ):
                if args[key] is not None:
                    value = broadcast(args[key], self.n)
                    target[fired] = value[fired]
        elif name in ("strategy.close", "strategy.close_all"):
            self.exits |= fired
        elif name == "strategy.exit":
            if any(
                args[key] is not None
                for key in ("trail_price", "trail_points", "trail_offset")
            ):
                self.warn("İz süren stop (trail_*) desteklenmiyor; yok sayıldı.")
            for key, target in (
                ("stop", self.stops),
                ("limit", self.limits),
                ("loss", self.loss_ticks),
                ("profit", self.profit_ticks),
            ):
                if args[key] is not None:
                    value = broadcast(args[key], self.n)
                    hit = fired & ~np.isnan(value)
                    target[hit] = value[hit]
        else:
            self.warn(f"{name} desteklenmiyor; yok sayıldı.")

    # Phase two: bar by bar -------------------------------------------------------------

    def prepare(self, statement: Node) -> None:
        """Precompute the vectorizable parts of a bar-by-bar statement."""
        if isinstance(statement, If):
            self.precompute(statement.cond)
            for inner in (*statement.body, *statement.orelse):
                if inner in self.analysis.dynamic:
                    self.prepare(inner)
            return
        if isinstance(statement, (Assign, Reassign)):
            node = statement.value
        elif isinstance(statement, ExprStmt):
            node = statement.expr
        else:
            return
        if isinstance(node, Call) and node.func in EFFECTS:
            for arg in (*node.args, *node.kwargs.values()):
                self.precompute(arg)
            self.placeholder(node)
        else:
            self.precompute(node)

    def placeholder(self, node: Call) -> None:
        """Register a plot/shape/alert that the bar-by-bar phase fills in."""
        name = node.func
        if name.startswith("strategy.") or name == "alert":
            return
        if name in ("plot", "plotshape", "plotchar", "alertcondition"):
            function = FUNCTIONS[name]
            nodes = _bind_nodes(function, node)
            args = {}
            for param in function.params:
                arg = nodes.get(param.name)
                if arg is None:
                    args[param.name] = (
                        None if param.default is REQUIRED else param.default
                    )
                elif isinstance(arg, (Num, Str, Bool, Color)):
                    args[param.name] = arg.value
                else:
                    args[param.name] = self.pre.get(arg, NAN)
            key = "series" if name != "alertcondition" else "condition"
            args[key] = np.full(self.n, NAN) if name == "plot" else False
            self.drawn(name, args, node)

    def precompute(self, node: Node) -> None:
        if isinstance(node, (Num, Str, Bool, Color, Na)):
            return
        if not self.analysis.is_dynamic(node):
            self.pre[node] = self.eval(node)
            return
        if isinstance(node, Index):
            self.precompute(node.offset)
            if not isinstance(node.target, Name):
                self.precompute_children(node.target)
            return
        self.precompute_children(node)

    def precompute_children(self, node: Node) -> None:
        if isinstance(node, Unary):
            self.precompute(node.operand)
        elif isinstance(node, Binary):
            self.precompute(node.left)
            self.precompute(node.right)
        elif isinstance(node, Ternary):
            self.precompute(node.cond)
            self.precompute(node.then)
            self.precompute(node.other)
        elif isinstance(node, TupleLit):
            for item in node.items:
                self.precompute(item)
        elif isinstance(node, Call):
            for arg in (*node.args, *node.kwargs.values()):
                self.precompute(arg)

    def run_bar_by_bar(self) -> None:
        analysis = self.analysis
        self.i_mode = True
        self.history = {name: [NAN] * self.n for name in analysis.assigned_dynamic}
        temporary = analysis.assigned_dynamic - analysis.var_names
        program = self.script.program
        for i in range(self.n):
            self.i = i
            if analysis.uses_position:
                self.sim.start(i)
            for name in temporary:
                self.state[name] = NAN
            self.block_at(program)
            for name, values in self.history.items():
                values[i] = self.state.get(name, NAN)
            if analysis.uses_position:
                self.sim.end(i)
        self.i_mode = False

    def block_at(self, statements: list[Node]) -> None:
        for statement in statements:
            if statement in self.analysis.dynamic:
                try:
                    self.statement_at(statement)
                except ScriptError as error:
                    raise error.located(statement.line, statement.col) from None
                except (ArithmeticError, ValueError, TypeError, IndexError) as error:
                    raise ScriptError(
                        f"Çalışma hatası: {error}", statement.line, statement.col
                    ) from None
            elif (
                isinstance(statement, If)
                and statement in self.analysis.contains_dynamic
            ):
                taken = self.conds[statement][self.i]
                self.block_at(statement.body if taken else statement.orelse)

    def statement_at(self, statement: Node) -> None:
        if isinstance(statement, If):
            taken = to_bool(self.eval_at(statement.cond))
            self.block_at(statement.body if taken else statement.orelse)
        elif isinstance(statement, Assign):
            if statement.mode != "decl":
                if all(target in self.initialized for target in statement.targets):
                    return
                self.initialized.update(statement.targets)
            value_node = statement.value
            if isinstance(value_node, Call) and value_node.func in EFFECTS:
                value = self.effect_at(value_node)
            else:
                value = self.eval_at(value_node)
            if len(statement.targets) > 1:
                if not isinstance(value, tuple) or len(value) != len(statement.targets):
                    raise ScriptError(
                        f"{len(statement.targets)} değişkene uygun sayıda değer atanamadı."
                    )
                for target, item in zip(statement.targets, value):
                    self.state[target] = item
            else:
                self.state[statement.targets[0]] = value
        elif isinstance(statement, Reassign):
            target = statement.target
            if target not in self.state and target not in self.env:
                raise ScriptError(f"'{target}' önce '=' ile tanımlanmalı.")
            current = self.state.get(target, NAN)
            value = self.eval_at(statement.value)
            if statement.op != ":=":
                value = arith(statement.op[0], current, value)
            self.state[target] = value
        elif isinstance(statement, ExprStmt):
            expr = statement.expr
            if isinstance(expr, Call) and expr.func in DECLARATIONS:
                return
            if isinstance(expr, Call) and expr.func in EFFECTS:
                self.effect_at(expr)
            else:
                self.eval_at(expr)

    def eval_at(self, node: Node, scope: dict | None = None) -> Any:
        """Evaluate an expression on the current bar."""
        i = self.i
        if node in self.pre:
            value = self.pre[node]
            if isinstance(value, np.ndarray):
                return value[i]
            if isinstance(value, tuple):
                return tuple(v[i] if isinstance(v, np.ndarray) else v for v in value)
            return value
        if isinstance(node, (Num, Str, Bool, Color)):
            return node.value
        if isinstance(node, Na):
            return NAN
        if isinstance(node, Name):
            name = node.id
            if scope is not None and name in scope:
                return scope[name]
            if name in self.state:
                return self.state[name]
            if name in DYNAMIC_BUILTINS:
                return self.sim.value(name)
            value = self.lookup(node, None)
            return value[i] if isinstance(value, np.ndarray) else value
        if isinstance(node, Index):
            return self.history_at(node, scope)
        if isinstance(node, Unary):
            value = self.eval_at(node.operand, scope)
            if node.op == "not":
                return not to_bool(value)
            value = to_float(value)
            return -value if node.op == "-" else value
        if isinstance(node, Binary):
            if node.op in ("and", "or"):
                left = to_bool(self.eval_at(node.left, scope))
                if node.op == "and" and not left:
                    return False
                if node.op == "or" and left:
                    return True
                return bool(to_bool(self.eval_at(node.right, scope)))
            return self.binary(
                node, self.eval_at(node.left, scope), self.eval_at(node.right, scope)
            )
        if isinstance(node, Ternary):
            branch = (
                node.then if to_bool(self.eval_at(node.cond, scope)) else node.other
            )
            return self.eval_at(branch, scope)
        if isinstance(node, TupleLit):
            return tuple(self.eval_at(item, scope) for item in node.items)
        if isinstance(node, Call):
            return self.call_at(node, scope)
        raise ScriptError("Bu ifade burada kullanılamaz.", node.line, node.col)

    def history_at(self, node: Index, scope: dict | None) -> Any:
        i = self.i
        offset = _history_offset(self.eval_at(node.offset, scope), node)
        if isinstance(offset, np.ndarray):
            offset = int(offset[i])
        target = node.target
        if isinstance(target, Name):
            name = target.id
            if scope is not None and name in scope:
                if offset:
                    raise ScriptError(
                        "Fonksiyon parametresinin geçmişi (x[1]) bar bar çalışan"
                        " ifadelerde kullanılamaz.",
                        node.line,
                        node.col,
                    )
                return scope[name]
            if name in self.history:
                if not offset:
                    return self.state.get(name, NAN)
                return self.history[name][i - offset] if i >= offset else NAN
        if scope is None and not self.analysis.is_dynamic(target):
            if target not in self.pre:
                self.pre[target] = self.eval(target)
            series = self.pre[target]
            if not isinstance(series, np.ndarray):
                return series
            return series[i - offset] if i >= offset else NAN
        value = self.eval_at(target, scope)
        key = (self.stack, target)
        past = self.expr_history.setdefault(key, [NAN] * self.n)
        past[i] = value
        if not offset:
            return value
        return past[i - offset] if i >= offset else NAN

    def call_at(self, node: Call, scope: dict | None) -> Any:
        if node.func in self.script.functions:
            return self.call_user(node, scope, lambda arg: self.eval_at(arg, scope))
        function = FUNCTIONS.get(node.func)
        if function is None:
            return self.drawing(node)
        if function.category == "input":
            return self.input_value(node)
        if function.varargs:
            values = [self.eval_at(arg, scope) for arg in node.args]
            return _scalar(function.impl(self.bars, values))
        args = self.bind(function, node, lambda arg: self.eval_at(arg, scope))
        if node.func not in SERIES_FUNCTIONS:
            converted = {}
            for param in function.params:
                value = args[param.name]
                if param.kind in ("int", "float"):
                    value = self.constant(function, param.name, value, node, param.kind)
                elif param.kind == "series":
                    value = to_float(value)
                converted[param.name] = value
            return _scalar(function.impl(self.bars, converted))
        return self.series_call(function, node, args)

    def series_call(self, function: Function, node: Call, args: dict) -> Any:
        """Run a series function on the history of its arguments so far."""
        i = self.i
        key = (self.stack, node)
        site = self.sites.setdefault(key, {})
        prefix = self.bars.prefix(i + 1)
        converted = {}
        for param in function.params:
            value = args[param.name]
            if param.kind in ("series", "cond") or (
                param.kind == "any" and not isinstance(value, str)
            ):
                past = site.get(param.name)
                if past is None:
                    dtype = bool if param.kind == "cond" else float
                    past = site[param.name] = np.full(
                        self.n, False if dtype is bool else NAN, dtype=dtype
                    )
                if isinstance(value, np.ndarray) and value.shape == (self.n,):
                    value = value[i]
                past[i] = to_bool(value) if param.kind == "cond" else to_float(value)
                converted[param.name] = past[: i + 1]
            elif param.kind in ("int", "float"):
                converted[param.name] = self.constant(
                    function, param.name, value, node, param.kind
                )
            else:
                converted[param.name] = value
        result = function.impl(prefix, converted)
        if isinstance(result, tuple):
            return tuple(_last(item) for item in result)
        return _last(result)

    def effect_at(self, node: Call) -> Any:
        i = self.i
        name = node.func
        function = FUNCTIONS[name]
        args = self.bind(function, node, self.eval_at)
        if name.startswith("strategy."):
            if not to_bool(args.get("when", True)):
                return None
            fired = np.zeros(self.n, dtype=bool)
            fired[i] = True
            self.order(name, args, node, fired)
            return None
        if name == "alert":
            fired = np.zeros(self.n, dtype=bool)
            fired[i] = True
            self.alerts.append(Alert("alert()", str(args["message"]), fired))
            return None
        handle = self.handles.get(node)
        if isinstance(handle, Plot):
            handle.values[i] = to_float(args["series"])
            return None
        if isinstance(handle, Shape):
            handle.mask[i] = bool(to_bool(args["series"]))
            return None
        if isinstance(handle, Alert):
            handle.mask[i] = bool(to_bool(args["condition"]))
            return None
        return None


def _scalar(value: Any) -> Any:
    if isinstance(value, np.ndarray) and value.ndim == 0:
        return value.item()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _last(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        if not len(value):
            return NAN
        item = value[-1]
        return bool(item) if value.dtype == bool else float(item)
    return value
