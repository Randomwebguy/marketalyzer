import json
from datetime import date

import pytest
from backtesting import Strategy

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.engine import (
    market_context,
    optimize_backtest,
    run_backtest,
    split_holdout,
)

OFFLINE = {"benchmark": None, "usd": False}
FREE = {"costs": BistCosts(commission_rate=0, bsmv_rate=0), "slippage": 0}


class AlwaysShort(Strategy):
    def init(self):
        pass

    def next(self):
        if not self.position:
            self.sell(size=10)


def test_run_backtest_summary(prices):
    report = run_backtest(
        strategy="sma_cross", data=prices, params={"fast": 5, "slow": 20}, **OFFLINE
    )
    summary = report.summary()
    assert summary["strategy"] == "SmaCross"
    assert summary["params"] == {"fast": 5, "slow": 20}
    assert summary["trades"] > 0
    assert summary["commissions"] > 0
    assert summary["start"] == "2024-01-02T00:00:00"
    assert "benchmark_return_pct" not in summary
    json.dumps(summary)
    assert (report.trades["Size"] % 1 == 0).all()
    assert len(report.equity_curve) == len(prices)


def test_costs_reduce_returns(prices):
    params = {"fast": 5, "slow": 20}
    free = run_backtest(data=prices, params=params, **FREE, **OFFLINE)
    costly = run_backtest(
        data=prices,
        params=params,
        costs=BistCosts(commission_rate=0.004),
        slippage=0.002,
        **OFFLINE,
    )
    assert free.summary()["commissions"] == 0
    assert costly.summary()["return_pct"] < free.summary()["return_pct"]


def test_risk_free_rate_lowers_sharpe(prices):
    base = run_backtest(data=prices, **OFFLINE).summary()["sharpe"]
    with_rate = run_backtest(data=prices, risk_free_rate=0.4, **OFFLINE)
    assert with_rate.summary()["sharpe"] < base
    assert len(with_rate.trades) > 0


def test_short_trades_warn(prices):
    with pytest.warns(UserWarning, match="Short selling on BIST"):
        run_backtest(strategy=AlwaysShort, data=prices, **OFFLINE)


def test_run_backtest_needs_symbol_or_data():
    with pytest.raises(ValueError, match="Give a symbol"):
        run_backtest(strategy="sma_cross", **OFFLINE)


def test_run_backtest_checks_columns(prices):
    with pytest.raises(ValueError, match="missing columns: Close"):
        run_backtest(data=prices.drop(columns="Close"), **OFFLINE)


def test_run_backtest_loads_symbol_and_market_context(fake_fetch, prices, as_rows):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    index = prices.copy()
    index["Close"] = 10_000.0
    index.iloc[-1, index.columns.get_loc("Close")] = 12_000.0
    fake_fetch.responses[("equity", "XU100")] = as_rows(index)
    fx = prices.copy()
    fx["Close"] = 40.0
    fx.iloc[-1, fx.columns.get_loc("Close")] = 50.0
    fake_fetch.responses[("currency", "USDTRY")] = as_rows(fx)

    report = run_backtest("THYAO", "sma_cross", start="2024-01-02", end="2025-02-21")
    summary = report.summary()
    assert summary["symbol"] == "THYAO"
    assert summary["benchmark"] == "XU100"
    assert summary["benchmark_return_pct"] == pytest.approx(20.0)
    assert summary["usdtry_change_pct"] == pytest.approx(25.0)
    expected_usd = ((1 + summary["return_pct"] / 100) / 1.25 - 1) * 100
    assert summary["return_usd_pct"] == pytest.approx(expected_usd, abs=1e-3)
    benchmark_call = next(c for c in fake_fetch.calls if c[1]["symbol"] == "XU100")
    assert benchmark_call[1]["adjustment"] == "splits_only"


def test_run_backtest_warns_about_price_jumps(fake_fetch, prices, as_rows):
    broken = prices.copy()
    broken.iloc[150:, :4] /= 2
    fake_fetch.responses[("equity", "THYAO")] = as_rows(broken)
    with pytest.warns(UserWarning, match="move more than 20%"):
        run_backtest("THYAO", **OFFLINE)


def test_market_context_warns_when_data_is_missing(fake_fetch):
    with pytest.warns(UserWarning, match="could not be loaded"):
        context = market_context(date(2024, 1, 2), date(2024, 12, 31), 10.0)
    assert context == {}


def test_split_holdout(prices):
    train, test = split_holdout(prices, 0.3)
    assert len(train) == 210
    assert len(test) == 90
    assert train.index[-1] < test.index[0]
    assert split_holdout(prices, None) == (prices, None)
    with pytest.raises(ValueError):
        split_holdout(prices, 1.5)
    with pytest.raises(ValueError):
        split_holdout(prices.iloc[:2], 0.3)


def test_optimize_backtest(prices):
    result = optimize_backtest(
        data=prices,
        param_grid={"fast": [5, 10], "slow": [20, 40]},
        holdout=0.3,
        **OFFLINE,
    )
    assert result.best_params["fast"] in (5, 10)
    assert result.best_params["slow"] in (20, 40)
    assert result.in_sample.summary()["end"] < result.out_of_sample.summary()["start"]
    assert result.out_of_sample.params == result.best_params
    assert len(result.heatmap) == 4
    json.dumps(result.summary())


def test_optimize_uses_the_strategy_grid_and_constraint(prices):
    result = optimize_backtest(
        data=prices,
        strategy="rsi_reversion",
        holdout=None,
        max_tries=5,
        random_state=0,
        **OFFLINE,
    )
    assert result.out_of_sample is None
    assert result.best_params["lower"] < result.best_params["upper"]


def test_optimize_needs_a_grid(prices):
    with pytest.raises(ValueError, match="no param_grid"):
        optimize_backtest(strategy=AlwaysShort, data=prices, **OFFLINE)


def test_plot_writes_html(prices, tmp_path):
    report = run_backtest(data=prices, **OFFLINE)
    path = report.plot(tmp_path / "report.html")
    assert path.read_text().lstrip().lower().startswith("<!doctype html")


def test_benchmark_jumps_warn(fake_fetch, prices, as_rows):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    index = prices.copy()
    index.iloc[200:, :4] /= 100
    fake_fetch.responses[("equity", "XU100")] = as_rows(index)
    with pytest.warns(UserWarning, match="XU100: 1 bar"):
        run_backtest("THYAO", usd=False)
