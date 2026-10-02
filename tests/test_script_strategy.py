import warnings

import numpy as np
import pandas as pd
import pytest

from marketalyzer.backtest import optimize_backtest, run_backtest
from marketalyzer.backtest.strategies import SmaCross, get_strategy, strategy_params
from marketalyzer.paper.signals import wants_long
from marketalyzer.scripting import (
    ScriptError,
    compile_script,
    default_grid,
    delete_script,
    get_script,
    list_scripts,
    load_script,
    save_script,
    script_strategies,
    script_strategy,
)
from marketalyzer.scripting.store import scripts_dir

STOP_SCRIPT = """strategy("stop")
if bar_index == 5
    strategy.entry("L", strategy.long)
    strategy.exit("SL", "L", stop=close * 0.97)
"""


@pytest.fixture
def trending(prices):
    return prices


def backtest(source_or_name, data, **params):
    if isinstance(source_or_name, str) and "\n" in source_or_name:
        source_or_name = script_strategy(source_or_name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return run_backtest(
            strategy=source_or_name,
            data=data,
            benchmark=None,
            usd=False,
            params=params or None,
        )


def test_script_matches_the_builtin_strategy(prices):
    builtin = backtest(SmaCross, prices, fast=5, slow=20)
    script = backtest("script:sma_cross", prices, fastLength=5, slowLength=20)
    assert script.strategy == "script:sma_cross"
    assert script.params == {"fastLength": 5, "slowLength": 20}
    columns = ["EntryBar", "ExitBar", "Size"]
    pd.testing.assert_frame_equal(
        builtin.trades[columns].reset_index(drop=True),
        script.trades[columns].reset_index(drop=True),
    )


def test_every_library_strategy_backtests(prices):
    strategies = script_strategies()
    assert {"script:sma_cross", "script:donchian_breakout"} <= set(strategies)
    for name in strategies:
        report = backtest(name, prices)
        assert report.summary()["trades"] is not None, name


def test_strategy_class_is_cached_and_exposes_inputs():
    first = get_strategy("script:rsi_reversion")
    assert first is get_strategy("script:rsi_reversion")
    assert strategy_params(first) == {"length": 14, "lower": 30, "upper": 70}
    assert first.param_grid["length"] == [7, 14, 21, 28]


def test_indicators_are_not_strategies():
    with pytest.raises(ScriptError, match="strateji değil"):
        script_strategy(load_script("rsi"))
    with pytest.raises(ValueError, match="Unknown script"):
        get_strategy("script:yok")


def test_stop_loss_from_strategy_exit():
    close = np.r_[np.full(8, 100.0), np.full(10, 90.0)]
    index = pd.bdate_range("2024-01-02", periods=len(close), name="Date")
    data = pd.DataFrame(
        {"Open": close, "High": close, "Low": close - 1, "Close": close},
        index=index,
    )
    trades = backtest(STOP_SCRIPT, data).trades
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["EntryBar"] == 6
    assert trade["ExitBar"] == 8  # the gap below 97 triggers the stop
    assert trade["ExitPrice"] == pytest.approx(90, rel=0.01)


@pytest.mark.parametrize(
    ("declaration", "size"),
    [
        ('strategy("q", default_qty_type=strategy.fixed, default_qty_value=10)', 10),
        ('strategy("q", default_qty_type=strategy.cash, default_qty_value=1000)', 10),
        (
            'strategy("q", default_qty_type=strategy.percent_of_equity, default_qty_value=50)',
            498,
        ),
    ],
)
def test_position_sizing(declaration, size):
    close = np.full(10, 100.0)
    index = pd.bdate_range("2024-01-02", periods=10, name="Date")
    data = pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close}, index=index
    )
    source = f"""{declaration}
if bar_index == 2
    strategy.entry("L", strategy.long)
"""
    trades = backtest(source, data).trades
    assert trades.iloc[0]["Size"] == size


def test_default_grid_from_bounds_and_defaults():
    script = compile_script(
        """a = input.int(10, minval=5, maxval=40, step=5)
b = input.float(2.0)
c = input.int(3, minval=1, maxval=1000)
d = input.bool(true)
"""
    )
    grid = default_grid(script.inputs)
    assert grid["a"] == [5, 10, 15, 20, 25, 30, 35, 40]
    assert grid["b"] == [1.0, 1.5, 2.0, 2.5, 3.0]
    assert len(grid["c"]) == 8 and grid["c"][0] == 1 and grid["c"][-1] == 1000
    assert "d" not in grid


def test_optimize_and_paper_signal_with_a_script(prices):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = optimize_backtest(
            strategy="script:sma_cross",
            data=prices,
            benchmark=None,
            usd=False,
            param_grid={"fastLength": [5, 10], "slowLength": [20, 40]},
        )
    assert set(result.best_params) == {"fastLength", "slowLength"}
    assert isinstance(wants_long("script:supertrend", prices), bool)


class TestStore:
    def test_library_is_listed(self):
        entries = {entry["name"]: entry for entry in list_scripts()}
        assert entries["sma_cross"]["builtin"] is True
        assert entries["sma_cross"]["kind"] == "strategy"
        assert entries["rsi"]["kind"] == "indicator"
        assert "kesince" in entries["sma_cross"]["description"]

    def test_save_override_and_delete(self):
        source = get_script("sma_cross")["source"].replace("10,", "12,", 1)
        saved = save_script("sma_cross", source)
        assert saved["builtin"] is False and saved["customized"] is True
        assert get_script("sma_cross")["inputs"][0]["default"] == 12
        assert (scripts_dir() / "sma_cross.pine").exists()
        assert delete_script("sma_cross") is True
        assert get_script("sma_cross")["builtin"] is True
        with pytest.raises(ValueError, match="silinemez"):
            delete_script("sma_cross")

    def test_scripts_with_errors_are_saved_and_reported(self):
        saved = save_script("bozuk", "x = (close")
        assert saved["error"]["line"] == 1
        assert saved["kind"] is None
        with pytest.raises(ScriptError):
            load_script("bozuk")
        assert "script:bozuk" not in script_strategies()

    @pytest.mark.parametrize("name", ["../x", "Büyük", "", "a" * 49, "a b"])
    def test_names_are_validated(self, name):
        with pytest.raises(ValueError, match="Script adı"):
            save_script(name, "plot(close)")

    def test_missing_script(self):
        with pytest.raises(KeyError):
            get_script("yok")
        with pytest.raises(KeyError):
            delete_script("yok")
