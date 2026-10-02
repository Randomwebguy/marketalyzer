import math

import numpy as np
import pandas as pd
import pytest

from marketalyzer.scripting import ScriptError, compile_script, reference, ta
from marketalyzer.scripting.parser import Assign, If, Reassign, parse, tokenize
from marketalyzer.scripting.runtime import infer_interval


def frame(close, index=None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    index = (
        index
        if index is not None
        else pd.bdate_range("2024-01-02", periods=len(close), name="Date")
    )
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 1,
            "Low": np.minimum(open_, close) - 1,
            "Close": close,
            "Volume": 1000.0,
        },
        index=index,
    )


def run(source: str, data=None, **inputs):
    data = data if data is not None else frame(np.arange(1, 41))
    return compile_script(source).run(data, inputs or None, symbol="THYAO")


def values(result, title=None):
    plot = (
        result.plots[0]
        if title is None
        else next(p for p in result.plots if p.title == title)
    )
    return plot.values


class TestParser:
    def test_pine_layout(self):
        source = """//@version=5
indicator("Deneme", overlay=true)
x = close > open ? 1 : -1  // yorum
if x > 0 and not (close < 2)
    y = 1
else if x < 0
    y = 2
else
    y = 3
total = ta.sma(close,
     10)
"""
        program = parse(source)
        assert isinstance(program[1], Assign)
        branch = program[2]
        assert isinstance(branch, If) and isinstance(branch.orelse[0], If)
        assert len(branch.orelse[0].orelse) == 1
        assert isinstance(program[3], Assign)

    def test_reassign_var_and_tuple(self):
        program = parse(
            "var float a = 0\na += 1\n[m, s, h] = ta.macd(close, 12, 26, 9)"
        )
        assert program[0].mode == "var"
        assert isinstance(program[1], Reassign) and program[1].op == "+="
        assert program[2].targets == ["m", "s", "h"]

    def test_trailing_operator_continues(self):
        program = parse("x = close +\n    open\nplot(x)")
        assert len(program) == 2

    @pytest.mark.parametrize(
        ("source", "line", "fragment"),
        [
            ("x = (close + 1\nplot(x)", 1, "kapatılmamış"),
            ("if close > 1\n  x = 1", 2, "4 boşluk"),
            ("for i = 0 to 10\n    x = i", 1, "'for'"),
            ("x = close $ 2", 1, "Geçersiz karakter"),
            ("x = 1\n    y = 2", 2, "girinti"),
        ],
    )
    def test_syntax_errors_have_positions(self, source, line, fragment):
        with pytest.raises(ScriptError) as error:
            compile_script(source)
        assert error.value.line == line
        assert fragment in error.value.message

    def test_tokens_keep_strings_and_colors(self):
        kinds = [(t.kind, t.value) for t in tokenize('c = #FF0000\ns = "a\\"b"')[:7]]
        assert ("color", "#FF0000") in kinds
        assert ("str", 'a"b') in kinds


class TestTa:
    def test_ema_is_seeded_with_sma(self):
        source = np.arange(1, 11, dtype=float)
        out = ta.ema(source, 3)
        assert np.isnan(out[:2]).all()
        assert out[2] == pytest.approx(2.0)
        assert out[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)

    def test_rma_and_rsi(self):
        rng = np.random.default_rng(0)
        close = 100 + np.cumsum(rng.normal(0, 1, 200))
        rsi = ta.rsi(close, 14)
        assert np.isnan(rsi[:14]).all() and not np.isnan(rsi[14])
        assert np.nanmin(rsi) >= 0 and np.nanmax(rsi) <= 100
        assert ta.rsi(np.arange(30, dtype=float), 14)[-1] == 100

    def test_crossings(self):
        a = np.array([1.0, 2, 3, 2, 1])
        b = np.full(5, 2.0)
        assert ta.crossover(a, b).tolist() == [False, False, True, False, False]
        assert ta.crossunder(a, b).tolist() == [False, False, False, False, True]

    def test_barssince_valuewhen_and_pivots(self):
        cond = np.array([False, True, False, False, True, False])
        assert np.isnan(ta.barssince(cond)[0])
        assert ta.barssince(cond)[1:].tolist() == [0, 1, 2, 0, 1]
        source = np.arange(6, dtype=float) * 10
        assert ta.valuewhen(cond, source, 0)[-1] == 40
        assert ta.valuewhen(cond, source, 1)[-1] == 10
        peak = np.array([1.0, 2, 5, 2, 1, 1])
        pivots = ta.pivothigh(peak, 2, 2)
        assert pivots[4] == 5 and np.isnan(pivots[:4]).all()

    def test_supertrend_flips_with_the_trend(self):
        close = np.r_[np.linspace(100, 150, 60), np.linspace(150, 90, 60)]
        line, direction = ta.supertrend(close + 1, close - 1, close, 3, 10)
        assert direction[50] == -1 and direction[-1] == 1
        assert line[50] < close[50] < line[-1] + 100

    def test_shift_bool_fills_false(self):
        assert ta.shift(np.array([True, True]), 1).tolist() == [False, True]


class TestRuntime:
    def test_plots_inputs_and_overrides(self):
        source = """indicator("SMA", overlay=true)
length = input.int(5, "Uzunluk", minval=2)
plot(ta.sma(close, length), "SMA")
hline(10, "On")
"""
        result = run(source)
        assert result.declaration.overlay is True
        assert values(result)[4] == pytest.approx(3.0)
        assert result.hlines[0].price == 10
        assert run(source, length=3).plots[0].values[2] == pytest.approx(2.0)
        with pytest.raises(ScriptError, match="en az 2"):
            run(source, length=1)
        with pytest.raises(ScriptError, match="Bilinmeyen parametre"):
            run(source, nope=1)

    def test_if_blocks_mask_assignments(self):
        source = """x = 0.0
if close % 2 == 0
    x := 1
else
    x := -1
plot(x)
"""
        out = values(run(source))
        assert out[:4].tolist() == [-1, 1, -1, 1]

    def test_var_counter_runs_bar_by_bar(self):
        source = """var count = 0
if close % 3 == 0
    count += 1
plot(count)
"""
        script = compile_script(source)
        assert script.describe()["stateful"] is True
        out = values(script.run(frame(np.arange(1, 13))))
        assert out.tolist() == [0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 4]

    def test_recursion_through_history(self):
        source = """e = 0.0
e := na(e[1]) ? close : 0.5 * close + 0.5 * e[1]
plot(e)
n = 0
n := nz(n[1]) + 1
plot(n, "Sayaç")
"""
        result = run(source)
        expected = [1.0]
        for price in range(2, 41):
            expected.append(0.5 * price + 0.5 * expected[-1])
        assert values(result) == pytest.approx(expected)
        assert values(result, "Sayaç")[-1] == 40

    def test_ta_on_stateful_series_matches_vectorized(self):
        source = """var acc = 0.0
acc := acc + close
plot(ta.sma(acc, 3), "Durumlu")
plot(ta.sma(ta.cum(close), 3), "Vektörel")
"""
        result = run(source)
        np.testing.assert_allclose(
            values(result, "Durumlu"), values(result, "Vektörel"), equal_nan=True
        )

    def test_user_functions(self):
        source = """double(x) => x * 2
band(src, len) =>
    mid = ta.sma(src, len)
    [mid, mid + 1]
[m, u] = band(close, 2)
plot(double(close), "İki kat")
plot(u - m, "Fark")
"""
        result = run(source)
        assert values(result, "İki kat")[0] == 2
        assert np.nanmax(values(result, "Fark")) == 1

    def test_ternary_colors_and_shapes(self):
        source = """indicator("x")
up = close > open
plot(close, color=up ? color.green : color.red)
plotshape(up, "Yukarı", style=shape.triangleup, location=location.belowbar)
alertcondition(up, "Yükseliş", "Kapanış açılışın üstünde")
"""
        result = run(source)
        assert result.plots[0].colors[1] == "#4caf50"
        assert result.shapes[0].mask[1] and result.shapes[0].location == "belowbar"
        assert result.alerts[0].mask.sum() == 39

    def test_history_cannot_look_ahead(self):
        with pytest.raises(ScriptError, match="negatif"):
            run("plot(close[-1])")

    def test_unknown_names_suggest_fixes(self):
        with pytest.raises(ScriptError, match="ta.sma") as error:
            compile_script("x = ta.smaa(close, 3)")
        assert error.value.line == 1
        with pytest.raises(ScriptError, match="Tanımsız değişken: clse"):
            run("plot(clse)")

    def test_argument_errors(self):
        with pytest.raises(ScriptError, match="'length' argümanı eksik"):
            run("plot(ta.sma(close))")
        with pytest.raises(ScriptError, match="sabit bir sayı"):
            run("plot(ta.sma(close, close))")
        with pytest.raises(ScriptError, match="desteklenmiyor"):
            compile_script('x = request.security("BIST:XU100", "D", close)')

    def test_plot_in_local_scope_is_rejected(self):
        with pytest.raises(ScriptError, match="yerel kapsamda"):
            run("if close > 1\n    plot(close)")

    def test_highest_defaults_to_high(self):
        result = run("plot(ta.highest(3))\nplot(ta.highest(close, 3))")
        assert result.plots[0].values[5] == result.plots[1].values[5] + 1

    def test_builtins_and_time(self):
        result = run(
            'plot(dayofweek)\nplot(bar_index)\nplot(syminfo.ticker == "THYAO" ? 1 : 0)'
        )
        assert result.plots[0].values[0] == 3  # 2024-01-02 was a Tuesday
        assert result.plots[1].values[-1] == 39
        assert result.plots[2].values[0] == 1

    def test_strategy_signals_and_short_entries(self):
        source = """strategy("s")
if close % 5 == 0
    strategy.entry("L", strategy.long)
if close % 7 == 0
    strategy.entry("S", strategy.short)
"""
        result = run(source)
        assert result.entries.sum() == 8 and result.exits.sum() == 5
        assert "açığa satış" in result.warnings[0]

    def test_strategy_calls_need_a_strategy_declaration(self):
        with pytest.raises(ScriptError, match="strategy"):
            run('indicator("x")\nstrategy.entry("L", strategy.long)')

    def test_position_size_is_simulated(self):
        source = """strategy("p")
if close == 5
    strategy.entry("L", strategy.long)
if close == 10
    strategy.close("L")
plot(strategy.position_size, "Pozisyon")
"""
        out = values(run(source), "Pozisyon")
        assert out[4] == 0 and out[5] == 1  # fills at the next open
        assert out[9] == 1 and out[10] == 0

    def test_drawings_are_ignored_with_a_warning(self):
        result = run('label.new(bar_index, high, "x")\nplot(close)')
        assert "çizim" in result.warnings[0]

    def test_infer_interval(self):
        assert infer_interval(pd.bdate_range("2024-01-01", periods=5)) == "1d"
        hourly = pd.date_range("2024-01-01 10:00", periods=5, freq="h")
        assert infer_interval(hourly) == "1h"
        assert infer_interval(pd.date_range("2024-01-01", periods=5, freq="W")) == "1W"


def test_reference_lists_functions_and_variables():
    entries = {entry["name"]: entry for entry in reference()}
    assert entries["ta.sma"]["signature"] == "ta.sma(source, length)"
    assert entries["ta.highest"]["category"] == "ta"
    assert entries["close"]["category"] == "değişken"
    assert entries["strategy.entry"]["category"] == "strateji"
    assert all(entry["doc"] for entry in entries.values())


def test_nan_handling_in_arithmetic():
    result = run("plot(close / 0)\nplot(nz(ta.sma(close, 3)))")
    assert np.isnan(result.plots[0].values).all()
    assert result.plots[1].values[0] == 0
    assert not math.isnan(result.plots[1].values[2])
