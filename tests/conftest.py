"""Shared fixtures. All prices here are synthetic, not market data."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from marketalyzer.backtest import data as data_module


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    """Keep the price cache inside the test's temporary directory."""
    path = tmp_path / "cache"
    monkeypatch.setenv("MARKETALYZER_CACHE_DIR", str(path))
    return path


@pytest.fixture
def prices() -> pd.DataFrame:
    """300 business days of an oscillating, slowly rising price series."""
    index = pd.bdate_range("2024-01-02", periods=300, name="Date")
    steps = np.arange(300)
    close = 100 + 15 * np.sin(steps / 15) + steps * 0.1
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) * 1.01,
            "Low": np.minimum(open_, close) * 0.99,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=index,
    )


@pytest.fixture
def as_rows():
    """Convert an OHLCV frame into the rows the provider returns."""

    def _as_rows(frame: pd.DataFrame) -> list[dict]:
        return [
            {
                "date": day.date(),
                "open": row.Open,
                "high": row.High,
                "low": row.Low,
                "close": row.Close,
                "volume": int(row.Volume),
            }
            for day, row in frame.iterrows()
        ]

    return _as_rows


@pytest.fixture
def fake_fetch(monkeypatch):
    """Replace the provider call with rows keyed by (kind, symbol)."""
    fake = SimpleNamespace(responses={}, calls=[])

    def _fetch(kind, params):
        fake.calls.append((kind, dict(params)))
        key = (kind, params["symbol"])
        if key not in fake.responses:
            raise ValueError(f"No data for {params['symbol']}.")
        return fake.responses[key]

    monkeypatch.setattr(data_module, "_fetch", _fetch)
    return fake
