import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from marketalyzer.paper import (
    cli as cli_module,
    feed as feed_module,
)
from marketalyzer.paper.account import PaperAccount
from marketalyzer.paper.cli import account_path, main


def run(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys, *args):
    code, out, err = run(capsys, "--json", *args)
    assert code == 0, err
    return json.loads(out)


def bars(start, periods, freq="5min", first=300.0):
    index = pd.date_range(start, periods=periods, freq=freq, name="Date")
    close = first + np.arange(periods) * 0.25
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1e4,
        },
        index=index,
    )


def v_shaped_daily(end, periods=80):
    """Daily bars that fall, then rise: SMA(5) ends up above SMA(20)."""
    days = pd.bdate_range(end=end, periods=periods, name="Date")
    half = periods // 2
    close = np.r_[np.linspace(260, 200, half), np.linspace(200, 300, periods - half)]
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6},
        index=days,
    )


@pytest.fixture
def account(capsys):
    assert run(capsys, "init", "--cash", "50000")[0] == 0
    return account_path("default")


def test_init_and_status(account, capsys):
    assert account.exists()
    status = run_json(capsys, "status")
    assert status["cash"] == 50_000
    assert status["positions"] == []
    code, out, _ = run(capsys, "status")
    assert "Nakit" in out
    assert "50,000.00 TL" in out


def test_init_refuses_existing_account(account, capsys):
    code, _, err = run(capsys, "init")
    assert code == 1
    assert "already exists" in err


def test_missing_account(capsys):
    code, _, err = run(capsys, "--account", "nope", "status")
    assert code == 1
    assert "marketalyzer-paper init" in err


def test_order_lifecycle(account, capsys):
    order = run_json(capsys, "buy", "thyao", "10", "--limit", "290")
    assert (order["symbol"], order["type"], order["tif"]) == ("THYAO", "limit", "day")
    assert order["tag"] == "manual"
    gtc = run_json(capsys, "buy", "GARAN", "5", "--stop", "130.5", "--gtc")
    assert (gtc["type"], gtc["tif"]) == ("stop", "gtc")

    assert len(run_json(capsys, "orders")) == 2
    cancelled = run_json(capsys, "cancel", str(order["id"]))
    assert cancelled["status"] == "cancelled"
    assert [o["id"] for o in run_json(capsys, "orders")] == [gtc["id"]]
    assert len(run_json(capsys, "orders", "--all")) == 2
    assert run_json(capsys, "fills") == []

    code, out, _ = run(capsys, "orders", "--all")
    assert "iptal" in out


def test_rejected_order(account, capsys):
    code, _, err = run(capsys, "buy", "THYAO", "10", "--limit", "290.1")
    assert code == 1
    assert "tick grid" in err
    code, _, err = run(capsys, "sell", "THYAO", "10")
    assert code == 1
    assert "short selling" in err


def test_run_once_processes_live_bars(account, capsys, monkeypatch):
    yesterday = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)
    frame = bars(yesterday + timedelta(hours=10), 6)
    monkeypatch.setattr(feed_module, "_fetch_intraday", lambda *args: frame)
    # Placed before yesterday's session, so yesterday's bars can fill it.
    with PaperAccount(account) as paper:
        paper.submit("THYAO", "buy", 10, now=yesterday + timedelta(hours=9))

    code, out, err = run(capsys, "--json", "run", "THYAO", "--once", "--delay", "0")
    assert code == 0, err
    step = json.loads(out)
    assert step["bars"] == 6
    assert step["fills"][0]["price"] == 300.25  # 300 + half the 0.1% slippage, on tick

    status = run_json(capsys, "status")
    assert status["positions"][0]["qty"] == 10
    assert status["positions"][0]["last_price"] == 301.25


def test_run_once_with_a_strategy(account, capsys, monkeypatch):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(
        feed_module,
        "_fetch_intraday",
        lambda *args: bars(today - timedelta(days=1) + timedelta(hours=10), 3),
    )
    daily = v_shaped_daily(today - timedelta(days=1))
    monkeypatch.setattr(feed_module, "load_ohlcv", lambda *args, **kwargs: daily)

    code, out, err = run(
        capsys,
        "run",
        "THYAO",
        "-s",
        "sma_cross",
        "-p",
        "fast=5",
        "-p",
        "slow=20",
        "--once",
        "--delay",
        "0",
    )
    assert code == 0, err
    assert "sinyal: THYAO=AL" in out
    assert "emir: AL" in out
    (order,) = run_json(capsys, "orders")
    assert order["tag"] == "signal:SmaCross"


def test_replay(capsys, monkeypatch):
    def fake_load(symbol, start, end, interval="1d", adjustment=None, **kwargs):
        if interval == "5m":
            return pd.concat(
                [bars("2026-09-29 10:00", 4), bars("2026-09-30 10:00", 4, first=310.0)]
            )
        return v_shaped_daily("2026-09-30")

    monkeypatch.setattr(cli_module, "load_ohlcv", fake_load)
    result = run_json(
        capsys,
        "replay",
        "THYAO",
        "-s",
        "sma_cross",
        "-p",
        "fast=5",
        "-p",
        "slow=20",
        "--start",
        "2026-09-29",
        "--end",
        "2026-09-30",
        "--save",
        "replay-test",
    )
    assert result["steps"] == 10  # 8 bar ends and 2 session closes
    assert [f["side"] for f in result["fills"]] == ["buy"]
    assert result["account"] == str(account_path("replay-test"))
    assert account_path("replay-test").exists()

    code, out, _ = run(
        capsys,
        "replay",
        "THYAO",
        "-s",
        "sma_cross",
        "--start",
        "2026-09-29",
        "--end",
        "2026-09-30",
    )
    assert code == 0
    assert "Al ve tut getirisi" in out
