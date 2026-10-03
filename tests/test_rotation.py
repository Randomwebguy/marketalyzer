"""Momentum rotation: monthly picks by past return, filled at the next open.

Prices come from the demo generator (synthetic, deterministic).
"""

from datetime import timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# Serve demo bars instead of Yahoo; keep the model list off the network.
from test_ai_lab import TOKEN, no_models, synthetic  # noqa: F401

from marketalyzer import rotation, services
from marketalyzer.backtest.costs import BistCosts
from marketalyzer.web.app import create_app

TODAY = services.today()
SYMBOLS = ["THYAO", "GARAN", "ASELS", "BIMAS", "AKBNK", "EREGL"]


def cfg(**changes):
    values = {
        "symbols": SYMBOLS,
        "start": TODAY - timedelta(days=400),
        "end": TODAY - timedelta(days=30),
        "top": 2,
    }
    values.update(changes)
    return rotation.RotationConfig(**values)


def test_picks_are_the_top_by_past_return_and_fill_next_open():
    config = cfg()
    prices = rotation.load(config)
    result = rotation.run_rotation(config, prices)
    assert len(result["rebalances"]) >= 10
    index = prices.closes.index
    lookback, skip = config.lookback_months * 21, config.skip_months * 21
    for row in result["rebalances"]:
        i = index.get_loc(pd.Timestamp(row["decided"]))
        scores = rotation.momentum_scores(prices.closes, i, lookback, skip)
        best = list(scores.sort_values(ascending=False).index[: config.top])
        assert [p["symbol"] for p in row["picks"]] == best
        assert row["executed"] == services.stamp(index[i + 1])
    # Decisions are month ends (and the day before the start).
    months = {r["decided"][:7] for r in result["rebalances"][1:]}
    assert len(months) == len(result["rebalances"]) - 1


def test_later_bars_do_not_change_earlier_rebalances():
    short = rotation.run_rotation(cfg(end=TODAY - timedelta(days=120)))
    long = rotation.run_rotation(cfg())
    shared = len(short["rebalances"]) - 1  # The short run's last may be cut off.
    assert shared > 3
    assert long["rebalances"][:shared] == short["rebalances"][:shared]


def test_costs_lower_the_result_and_trades_are_recorded():
    free = rotation.run_rotation(
        cfg(costs=BistCosts(commission_rate=0.0), slippage=0.0)
    )
    paid = rotation.run_rotation(cfg())
    assert paid["rotation"]["return_pct"] < free["rotation"]["return_pct"]
    assert paid["rotation"]["trades"] == len(paid["trades"]) > 0
    for trade in paid["trades"]:
        assert trade["exit_time"] > trade["entry_time"]
    assert paid["equal"]["return_pct"] is not None
    assert paid["benchmark"]["symbol"] == "XU100"


def test_filters_leave_slots_in_cash():
    result = rotation.run_rotation(cfg(absolute=True))
    for row in result["rebalances"]:
        assert all(p["above_200"] for p in row["picks"])
        assert row["cash_slots"] == cfg().top - len(row["picks"])
    market = rotation.run_rotation(cfg(market=True))
    for row in market["rebalances"]:
        if not row["market_up"]:
            assert row["picks"] == []


def test_current_picks_rank_as_of_the_last_close():
    config = cfg()
    prices = rotation.load(config)
    result = rotation.run_rotation(config, prices)
    current = result["current"]
    i = len(prices.closes.index) - 1
    scores = rotation.momentum_scores(prices.closes, i, 6 * 21, 21)
    best = list(scores.sort_values(ascending=False).index[: config.top])
    assert [p["symbol"] for p in current["picks"]] == best
    assert len(current["ranking"]) == len(SYMBOLS)
    assert current["as_of"] == services.stamp(prices.closes.index[-1])


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"symbols": ["THYAO"]}, "en az 2"),
        ({"top": 7}, "hisse sayısından"),
        ({"lookback_months": 2}, "Momentum ufku"),
        ({"skip_months": 6}, "Atlanan"),
        ({"start": TODAY, "end": TODAY - timedelta(days=5)}, "bitişten önce"),
    ],
)
def test_config_checks(changes, message):
    with pytest.raises(ValueError, match=message):
        cfg(**changes).check()


def test_web_rotation():
    app = create_app(TOKEN)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        body = {
            "symbols": SYMBOLS,
            "start": (TODAY - timedelta(days=300)).isoformat(),
            "top": 2,
            "lookback_months": 6,
        }
        result = client.post("/api/lab/rotation", json=body).json()
        assert result["rotation"]["trades"] > 0 and result["current"]["picks"]
        bad = client.post("/api/lab/rotation", json={**body, "top": 9})
        assert bad.status_code == 400
