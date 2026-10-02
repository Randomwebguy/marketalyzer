"""Run strategy scripts as backtesting.py strategies.

A script declared with ``strategy()`` becomes a ``Strategy`` subclass whose
parameters are the script's inputs, so it can be backtested, optimized, walked
forward and paper traded exactly like the built-in strategies. Orders follow
Pine's defaults: a signal on a bar's close fills at the next bar's open, and
entries are ignored while a position is open.
"""

from __future__ import annotations

import hashlib
import math
import sys
from typing import Any, ClassVar

import numpy as np
from backtesting import Strategy

from marketalyzer.backtest.costs import tick_size
from marketalyzer.scripting.errors import ScriptError
from marketalyzer.scripting.runtime import InputSpec, Script, compile_script

MAX_GRID_VALUES = 8
FULL_EQUITY = 1 - sys.float_info.epsilon
_CLASSES: dict[str, type[ScriptStrategy]] = {}


def _finite(value: float) -> bool:
    return value is not None and not math.isnan(value)


class ScriptStrategy(Strategy):
    """Base class for script strategies; create them with :func:`script_strategy`."""

    script: ClassVar[Script]
    param_names: ClassVar[tuple[str, ...]] = ()
    param_grid: ClassVar[dict[str, list]] = {}

    def init(self):
        """Run the script over all bars; it is causal, so no bar sees the future."""
        inputs = {name: getattr(self, name) for name in self.param_names}
        self.result = self.script.run(self.data.df, inputs)
        self._levels = [math.nan] * 4

    def next(self):
        """Act on the script's signals for the bar that just closed."""
        result = self.result
        i = len(self.data) - 1
        if self.position:
            if result.exits[i]:
                self.position.close()
                self._levels = [math.nan] * 4
                return
            self._update_exits(i)
            return
        if not self.orders:
            self._levels = [math.nan] * 4
        if result.entries[i]:
            self._enter(i)

    def _remember_levels(self, i: int) -> None:
        result = self.result
        arrays = (result.stops, result.limits, result.loss_ticks, result.profit_ticks)
        for k, values in enumerate(arrays):
            if _finite(values[i]):
                self._levels[k] = float(values[i])

    def _exit_prices(self, entry: float) -> tuple[float, float]:
        stop, limit, loss, profit = self._levels
        if not _finite(stop) and _finite(loss):
            stop = entry - loss * tick_size(entry)
        if not _finite(limit) and _finite(profit):
            limit = entry + profit * tick_size(entry)
        return stop, limit

    def _update_exits(self, i: int) -> None:
        self._remember_levels(i)
        for trade in self.trades:
            stop, limit = self._exit_prices(trade.entry_price)
            stop = float(stop) if _finite(stop) and stop > 0 else None
            limit = float(limit) if _finite(limit) and limit > 0 else None
            if trade.sl != stop:
                trade.sl = stop
            if trade.tp != limit:
                trade.tp = limit

    def _enter(self, i: int) -> None:
        size = self._size(i)
        if size is None:
            return
        result = self.result
        orders: dict[str, Any] = {}
        limit = result.entry_limit[i]
        stop = result.entry_stop[i]
        if _finite(limit):
            orders["limit"] = float(limit)
        if _finite(stop):
            orders["stop"] = float(stop)
        self._remember_levels(i)
        reference = orders.get("limit", orders.get("stop", self.data.Close[-1]))
        sl, tp = self._exit_prices(reference)
        if _finite(sl) and sl < reference:
            orders["sl"] = float(sl)
        if _finite(tp) and tp > reference:
            orders["tp"] = float(tp)
        for order in list(self.orders):
            if not order.is_contingent:
                order.cancel()
        self.buy(size=size, **orders)

    def _size(self, i: int) -> float | None:
        declaration = self.script.declaration
        qty = self.result.entry_qty[i]
        kind = declaration.qty_type
        value = float(qty) if _finite(qty) else declaration.qty_value
        price = self.data.Close[-1]
        if kind is None:
            if value is None:
                return FULL_EQUITY
            kind = "fixed"
        if kind == "percent_of_equity":
            fraction = (value if value is not None else 100) / 100
            if fraction <= 0:
                return None
            return FULL_EQUITY if fraction >= 1 else fraction
        if kind == "cash":
            shares = math.floor((value or 0) / price) if price > 0 else 0
        else:
            shares = math.floor(value if value is not None else 1)
        return shares if shares >= 1 else None


def _grid_values(spec: InputSpec) -> list:
    integer = spec.kind == "int"
    low, high, step = spec.minval, spec.maxval, spec.step
    if spec.options:
        return list(spec.options)
    if low is not None and high is not None and high > low:
        step = step or (1 if integer else (high - low) / (MAX_GRID_VALUES - 1))
        count = math.floor((high - low) / step + 1e-9) + 1
        if count <= MAX_GRID_VALUES:
            values = [low + k * step for k in range(count)]
        else:
            values = list(np.linspace(low, high, MAX_GRID_VALUES))
            values = [low + round((v - low) / step) * step for v in values]
    else:
        default = spec.default
        values = [default * factor for factor in (0.5, 0.75, 1, 1.25, 1.5)]
        values = [
            v
            for v in values
            if (low is None or v >= low) and (high is None or v <= high)
        ]
    if integer:
        values = [int(round(v)) for v in values]
        values = [v for v in values if v >= (low if low is not None else 1)]
    else:
        values = [round(float(v), 6) for v in values]
    return sorted(set(values))


def default_grid(inputs: list[InputSpec]) -> dict[str, list]:
    """Build an optimization grid from the inputs' ``minval``/``maxval``/``step``.

    Inputs without bounds are tried at 0.5x-1.5x their default. Text, boolean and
    source inputs are left at their defaults.
    """
    grid = {}
    for spec in inputs:
        if spec.kind not in ("int", "float"):
            continue
        values = _grid_values(spec)
        if len(values) > 1:
            grid[spec.name] = values
    return grid


def script_strategy(
    source: str | Script, name: str | None = None
) -> type[ScriptStrategy]:
    """Return a ``Strategy`` class for a strategy script.

    Classes are cached by name and source, and registered in this module so
    backtesting.py can pickle them for parallel optimization.
    """
    script = source if isinstance(source, Script) else compile_script(source, name)
    if script.declaration.kind != "strategy":
        raise ScriptError(
            "Bu script bir strateji değil. Backtest için strategy(...) ile"
            " bildirilmeli ve strategy.entry / strategy.close kullanmalı."
        )
    digest = hashlib.sha256(f"{script.name}\0{script.source}".encode()).hexdigest()[:16]
    cached = _CLASSES.get(digest)
    if cached is not None:
        return cached
    qualname = f"_ScriptStrategy_{digest}"
    attrs: dict[str, Any] = {spec.name: spec.default for spec in script.inputs}
    attrs.update(
        script=script,
        param_names=tuple(spec.name for spec in script.inputs),
        param_grid=default_grid(script.inputs),
        __module__=__name__,
        __qualname__=qualname,
        __doc__=f"Script strategy: {script.declaration.title}",
    )
    cls = type(qualname, (ScriptStrategy,), attrs)
    cls.__name__ = f"script:{script.name}" if script.name else "script"
    globals()[qualname] = cls
    _CLASSES[digest] = cls
    return cls
