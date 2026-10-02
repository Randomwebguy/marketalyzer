import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from backtesting import Strategy

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.paper.account import PaperAccount
from marketalyzer.walkforward import split_windows, walk_forward

FREE = BistCosts(commission_rate=0, bsmv_rate=0)


class AboveLevel(Strategy):
    """Long while the close is above ``level``; ``level`` is the tuned parameter."""

    level = 100

    param_grid = {"level": [90, 100, 110]}

    def init(self):
        pass

    def next(self):
        if self.data.Close[-1] > self.level and not self.position:
            self.buy()
        elif self.data.Close[-1] <= self.level and self.position:
            self.position.close()


def frame(closes, start="2025-01-01"):
    index = pd.bdate_range(start, periods=len(closes), name="Date")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1e6},
        index=index,
    )


def test_split_windows():
    index = pd.bdate_range("2025-01-01", periods=10)
    windows = split_windows(index, train_bars=4, test_bars=3)
    assert [(len(train), len(test)) for train, test in windows] == [
        (4, 3),
        (4, 3),
    ]
    assert windows[1][0][0] == index[3]
    assert windows[0][1][-1] == index[6]
    assert windows[1][1][0] == index[7]
    with pytest.raises(ValueError, match="more than 10"):
        split_windows(index, 10, 3)


def test_walk_forward_trades_only_the_test_periods(tmp_path):
    closes = np.r_[np.linspace(95, 120, 30), np.linspace(120, 105, 30)]
    signals = frame(closes)
    bars = signals.copy()
    bars["Dividend"] = 0.0
    bars.iloc[45, bars.columns.get_loc("Dividend")] = 2.0
    seen = []

    report = walk_forward(
        "thyao",
        AboveLevel,
        start="2025-01-01",
        train_bars=20,
        test_bars=20,
        costs=FREE,
        slippage=0.0,
        dividend_tax=0.0,
        signals=signals,
        bars=bars,
        account_path=tmp_path / "wf.sqlite",
        on_window=seen.append,
    )
    summary = report.summary()
    with PaperAccount(tmp_path / "wf.sqlite") as account:
        fills = account.fills()

    assert [w.test_start for w in report.windows] == [
        signals.index[20].date(),
        signals.index[40].date(),
    ]
    assert seen == report.windows
    # Every window was tuned on its own training period.
    assert all(w.params["level"] in (90, 100, 110) for w in report.windows)
    # The first order is decided at the last training close, filled at the first
    # test open.
    first = report.windows[0]
    assert first.trades >= 1
    assert summary["start"] == first.test_start.isoformat()
    assert summary["end"] == signals.index[-1].date().isoformat()
    # The dividend goes to the shares held when the ex-date session opened.
    ex_date = bars.index[45].date()
    held = sum(
        f.qty if f.side == "buy" else -f.qty for f in fills if f.time.date() < ex_date
    )
    assert summary["dividends"] == pytest.approx(2.0 * held)
    assert summary["trades"] == len(fills)
    assert all(f.time.date() >= first.test_start for f in fills)
    assert summary["max_drawdown_pct"] <= 0
    growth = np.prod([1 + w.forward_return_pct / 100 for w in report.windows])
    assert summary["return_pct"] == pytest.approx((growth - 1) * 100, abs=1e-3)
    assert summary["buy_hold_return_pct"] == pytest.approx(
        (closes[-1] / closes[20] - 1) * 100, abs=1e-3
    )
    json.dumps(summary)


def test_walk_forward_saves_the_account(tmp_path):
    signals = frame(np.linspace(95, 120, 40))
    report = walk_forward(
        "THYAO",
        AboveLevel,
        start=date(2025, 1, 1),
        train_bars=20,
        test_bars=20,
        signals=signals,
        bars=signals,
        account_path=tmp_path / "wf.sqlite",
    )
    assert report.summary()["account"] == str(tmp_path / "wf.sqlite")
    assert (tmp_path / "wf.sqlite").exists()
