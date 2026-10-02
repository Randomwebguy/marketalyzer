"""Run and optimize BIST backtests with backtesting.py."""

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from warnings import warn

import pandas as pd
from backtesting import Backtest, Strategy
from backtesting.lib import compute_stats

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.data import load_fx, load_ohlcv, suspicious_jumps
from marketalyzer.backtest.strategies import get_strategy, strategy_params

SUMMARY_FIELDS = {
    "Start": "start",
    "End": "end",
    "Duration": "duration_days",
    "Return [%]": "return_pct",
    "Buy & Hold Return [%]": "buy_hold_return_pct",
    "CAGR [%]": "cagr_pct",
    "Volatility (Ann.) [%]": "volatility_ann_pct",
    "Sharpe Ratio": "sharpe",
    "Sortino Ratio": "sortino",
    "Max. Drawdown [%]": "max_drawdown_pct",
    "# Trades": "trades",
    "Win Rate [%]": "win_rate_pct",
    "Profit Factor": "profit_factor",
    "Exposure Time [%]": "exposure_pct",
    "Equity Final [$]": "equity_final",
    "Commissions [$]": "commissions",
}
# backtesting.py leaves these statistics out instead of reporting zero.
SUMMARY_DEFAULTS = {"Commissions [$]": 0.0}


def _plain(value: Any) -> Any:
    """Convert a statistic into a JSON-friendly value."""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return value.days
    if isinstance(value, float):
        return None if math.isnan(value) else round(float(value), 4)
    if hasattr(value, "item"):
        return _plain(value.item())
    return value


@dataclass
class BacktestReport:
    """The result of one backtest run."""

    strategy: str
    params: dict[str, Any]
    stats: pd.Series
    backtest: Backtest = field(repr=False)
    symbol: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    data: pd.DataFrame | None = field(default=None, repr=False)

    @property
    def trades(self) -> pd.DataFrame:
        """Closed trades, one row per trade."""
        return self.stats["_trades"]

    @property
    def equity_curve(self) -> pd.DataFrame:
        """Equity and drawdown on every bar."""
        return self.stats["_equity_curve"]

    def summary(self) -> dict[str, Any]:
        """Key metrics as a JSON-friendly dict; amounts are in TRY."""
        result: dict[str, Any] = {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "params": self.params,
        }
        for key, name in SUMMARY_FIELDS.items():
            result[name] = _plain(self.stats.get(key, SUMMARY_DEFAULTS.get(key)))
        result.update({key: _plain(value) for key, value in self.context.items()})
        return result

    def plot(self, filename: str | Path, open_browser: bool = False) -> Path:
        """Save the interactive backtesting.py chart as an HTML file."""
        path = Path(filename)
        self.backtest.plot(
            results=self.stats, filename=str(path), open_browser=open_browser
        )
        return path


@dataclass
class OptimizationResult:
    """Parameters tuned on the training period and tested on the held-out one."""

    best_params: dict[str, Any]
    in_sample: BacktestReport
    out_of_sample: BacktestReport | None
    heatmap: pd.Series = field(repr=False)

    def summary(self) -> dict[str, Any]:
        """Both reports as a JSON-friendly dict."""
        return {
            "best_params": self.best_params,
            "in_sample": self.in_sample.summary(),
            "out_of_sample": self.out_of_sample.summary()
            if self.out_of_sample
            else None,
        }


def _warn_about_jumps(symbol: str, data: pd.DataFrame) -> None:
    jumps = suspicious_jumps(data)
    if not jumps.empty:
        days = ", ".join(str(day.date()) for day in jumps.index[:5])
        warn(
            f"{symbol}: {len(jumps)} bar(s) move more than 20% ({days}),"
            " beyond BIST's daily price limit. Check for an unadjusted split,"
            " bonus issue or index redenomination before trusting the result."
        )


def _prepare(
    symbol: str | None,
    data: pd.DataFrame | None,
    start: date | str | None,
    end: date | str | None,
    interval: str,
) -> pd.DataFrame:
    if data is None:
        if not symbol:
            raise ValueError("Give a symbol to load data for, or pass data.")
        data = load_ohlcv(symbol, start, end, interval)
        if interval not in ("1W", "1M"):
            _warn_about_jumps(symbol, data)
    missing = {"Open", "High", "Low", "Close"} - set(data.columns)
    if missing:
        raise ValueError(f"Data is missing columns: {', '.join(sorted(missing))}.")
    return data


def _backtest(
    data: pd.DataFrame,
    strategy: type[Strategy],
    cash: float,
    costs: BistCosts | None,
    slippage: float,
    trade_on_close: bool,
) -> Backtest:
    return Backtest(
        data,
        strategy,
        cash=cash,
        commission=costs or BistCosts(),
        spread=slippage,
        trade_on_close=trade_on_close,
        finalize_trades=True,
    )


def _period_return(frame: pd.DataFrame) -> float:
    close = frame["Close"]
    return (close.iloc[-1] / close.iloc[0] - 1) * 100


def market_context(
    start: date,
    end: date,
    return_pct: float,
    benchmark: str | None = "XU100",
    usd: bool = True,
) -> dict[str, Any]:
    """Compare a TRY return with a benchmark index and with USD/TRY.

    TRY returns are inflated by the lira's depreciation, so a strategy is only
    convincing if it also beats the index and holds up in USD terms.
    """
    context: dict[str, Any] = {}
    if benchmark:
        try:
            index = load_ohlcv(benchmark, start, end, adjustment="splits_only")
            _warn_about_jumps(benchmark, index)
            context["benchmark"] = benchmark
            context["benchmark_return_pct"] = _period_return(index)
        except Exception as error:
            warn(f"Benchmark {benchmark} could not be loaded: {error}")
    if usd:
        try:
            change = _period_return(load_fx("USDTRY", start, end))
            context["usdtry_change_pct"] = change
            context["return_usd_pct"] = (
                (1 + return_pct / 100) / (1 + change / 100) - 1
            ) * 100
        except Exception as error:
            warn(f"USDTRY could not be loaded: {error}")
    return context


def _report(
    backtest: Backtest,
    stats: pd.Series,
    data: pd.DataFrame,
    strategy: type[Strategy],
    symbol: str | None,
    risk_free_rate: float,
    benchmark: str | None,
    usd: bool,
) -> BacktestReport:
    if risk_free_rate:
        stats = compute_stats(stats=stats, data=data, risk_free_rate=risk_free_rate)
    trades = stats["_trades"]
    if not trades.empty and (trades["Size"] < 0).any():
        warn(
            "The strategy opened short positions. Short selling on BIST is"
            " restricted, so these trades may not be possible in practice."
        )
    instance = stats["_strategy"]
    params = {
        name: _plain(getattr(instance, name)) for name in strategy_params(strategy)
    }
    context = {}
    if benchmark or usd:
        context = market_context(
            stats["Start"].date(),
            stats["End"].date(),
            stats["Return [%]"],
            benchmark,
            usd,
        )
    return BacktestReport(
        strategy=strategy.__name__,
        params=params,
        stats=stats,
        backtest=backtest,
        symbol=symbol,
        context=context,
        data=data,
    )


def run_backtest(
    symbol: str | None = None,
    strategy: str | type[Strategy] = "sma_cross",
    *,
    data: pd.DataFrame | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
    interval: str = "1d",
    cash: float = 100_000,
    costs: BistCosts | None = None,
    slippage: float = 0.001,
    trade_on_close: bool = False,
    risk_free_rate: float = 0.0,
    benchmark: str | None = "XU100",
    usd: bool = True,
    params: dict[str, Any] | None = None,
) -> BacktestReport:
    """Backtest a strategy on one BIST symbol with BIST trading costs.

    Parameters
    ----------
    symbol
        BIST code such as THYAO. Optional when ``data`` is given.
    strategy
        A name from ``STRATEGIES`` or a backtesting.py ``Strategy`` subclass.
    data
        OHLCV frame to use instead of loading ``symbol`` from the provider.
    start, end, interval
        Date range and bar size to load. Defaults to the last year of daily bars.
    cash
        Starting cash in TRY. Orders are sized in whole shares.
    costs
        Commission and taxes, charged on entry and exit. Defaults to
        ``BistCosts()``.
    slippage
        Bid-ask spread and slippage as a fraction of price. backtesting.py
        charges it once per trade, on entry, so it is the round-trip cost.
    trade_on_close
        Fill market orders at the signal bar's close (BIST's closing session)
        instead of the next bar's open.
    risk_free_rate
        Annual TRY risk-free rate for Sharpe and Sortino, e.g. a deposit rate.
    benchmark, usd
        Also report the benchmark index return and the return in USD terms.
    params
        Strategy parameters overriding the class defaults.
    """
    strategy_cls = get_strategy(strategy)
    frame = _prepare(symbol, data, start, end, interval)
    backtest = _backtest(frame, strategy_cls, cash, costs, slippage, trade_on_close)
    stats = backtest.run(**(params or {}))
    return _report(
        backtest,
        stats,
        frame,
        strategy_cls,
        symbol,
        risk_free_rate,
        benchmark,
        usd,
    )


def split_holdout(
    data: pd.DataFrame, holdout: float | None
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Split bars into a training period and the last ``holdout`` share of bars."""
    if not holdout:
        return data, None
    if not 0 < holdout < 1:
        raise ValueError("holdout must be between 0 and 1.")
    cut = int(len(data) * (1 - holdout))
    if cut < 2 or len(data) - cut < 2:
        raise ValueError("Not enough bars to hold out a test period.")
    return data.iloc[:cut], data.iloc[cut:]


def optimize_backtest(
    symbol: str | None = None,
    strategy: str | type[Strategy] = "sma_cross",
    *,
    data: pd.DataFrame | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
    interval: str = "1d",
    param_grid: dict[str, list] | None = None,
    maximize: str = "Sharpe Ratio",
    holdout: float | None = 0.3,
    max_tries: int | None = None,
    random_state: int | None = None,
    cash: float = 100_000,
    costs: BistCosts | None = None,
    slippage: float = 0.001,
    trade_on_close: bool = False,
    risk_free_rate: float = 0.0,
    benchmark: str | None = "XU100",
    usd: bool = True,
) -> OptimizationResult:
    """Tune strategy parameters, then test them on bars the search never saw.

    The search runs on the first ``1 - holdout`` of the bars. The best parameters
    are then backtested on the rest, so an over-fitted strategy shows up as a gap
    between the two reports. The test period starts without indicator history,
    so a slow indicator trades later there than it would in a continuous run.
    """
    strategy_cls = get_strategy(strategy)
    grid = param_grid or getattr(strategy_cls, "param_grid", None)
    if not grid:
        raise ValueError(
            f"{strategy_cls.__name__} has no param_grid; pass param_grid explicitly."
        )
    frame = _prepare(symbol, data, start, end, interval)
    train, test = split_holdout(frame, holdout)

    backtest = _backtest(train, strategy_cls, cash, costs, slippage, trade_on_close)
    stats, heatmap = backtest.optimize(
        **{name: list(values) for name, values in grid.items()},
        maximize=maximize,
        constraint=getattr(strategy_cls, "constraint", None),
        max_tries=max_tries,
        random_state=random_state,
        return_heatmap=True,
    )
    best = {name: _plain(getattr(stats["_strategy"], name)) for name in grid}
    in_sample = _report(
        backtest,
        stats,
        train,
        strategy_cls,
        symbol,
        risk_free_rate,
        benchmark,
        usd,
    )
    out_of_sample = None
    if test is not None:
        out_of_sample = run_backtest(
            symbol,
            strategy_cls,
            data=test,
            cash=cash,
            costs=costs,
            slippage=slippage,
            trade_on_close=trade_on_close,
            risk_free_rate=risk_free_rate,
            benchmark=benchmark,
            usd=usd,
            params=best,
        )
    return OptimizationResult(best, in_sample, out_of_sample, heatmap)
