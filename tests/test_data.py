from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from marketalyzer.backtest.data import (
    load_fx,
    load_ohlcv,
    suspicious_jumps,
    to_frame,
)
from openbb_bist.models.equity_historical import BistEquityHistoricalData

IST = ZoneInfo("Europe/Istanbul")


def row(day, close, volume=1_000):
    return {
        "date": day,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": volume,
    }


def test_to_frame_daily_rows():
    frame = to_frame(
        [row(date(2026, 9, 29), 302.5), row(date(2026, 9, 28), 304.0, None)]
    )
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert list(frame.index) == [pd.Timestamp("2026-09-28"), pd.Timestamp("2026-09-29")]
    assert frame.index.name == "Date"
    assert frame["Volume"].tolist() == [0.0, 1_000.0]
    assert (frame.dtypes == "float64").all()


def test_to_frame_intraday_rows_use_istanbul_wall_clock():
    first = datetime(2026, 10, 2, 10, 0, tzinfo=IST)
    frame = to_frame([row(first, 1.0), row(first + timedelta(minutes=5), 2.0)])
    assert frame.index.tz is None
    assert frame.index[0] == pd.Timestamp("2026-10-02 10:00")


def test_to_frame_accepts_provider_models_and_drops_duplicates():
    models = [
        BistEquityHistoricalData.model_validate(row(date(2026, 9, 28), 304.0)),
        BistEquityHistoricalData.model_validate(row(date(2026, 9, 28), 305.0)),
    ]
    frame = to_frame(models)
    assert len(frame) == 1
    assert frame["Close"].iloc[0] == 305.0


def test_to_frame_rejects_empty_rows():
    with pytest.raises(ValueError):
        to_frame([])


def test_load_ohlcv_caches_closed_ranges(fake_fetch, prices, as_rows, cache_dir):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    first = load_ohlcv("thyao", "2024-01-02", "2025-02-21")
    second = load_ohlcv("THYAO", date(2024, 1, 2), date(2025, 2, 21))

    assert len(fake_fetch.calls) == 1
    kind, params = fake_fetch.calls[0]
    assert params == {
        "symbol": "THYAO",
        "interval": "1d",
        "adjustment": "splits_and_dividends",
        "start_date": date(2024, 1, 2),
        "end_date": date(2025, 2, 21),
    }
    assert list(cache_dir.iterdir())
    pd.testing.assert_frame_equal(first, second, check_freq=False)


def test_load_ohlcv_does_not_cache_open_ranges(fake_fetch, prices, as_rows, cache_dir):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    load_ohlcv("THYAO", "2024-01-02")
    load_ohlcv("THYAO", "2024-01-02")
    assert len(fake_fetch.calls) == 2
    assert not cache_dir.exists()


def test_load_ohlcv_cache_can_be_disabled(fake_fetch, prices, as_rows):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    load_ohlcv("THYAO", "2024-01-02", "2025-02-21", cache=False)
    load_ohlcv("THYAO", "2024-01-02", "2025-02-21", cache=False)
    assert len(fake_fetch.calls) == 2


def test_load_fx_uses_the_currency_fetcher(fake_fetch, prices, as_rows):
    fake_fetch.responses[("currency", "USDTRY")] = as_rows(prices)
    frame = load_fx("usdtry", "2024-01-02", "2025-02-21")
    assert fake_fetch.calls[0][0] == "currency"
    assert len(frame) == len(prices)


def test_suspicious_jumps_flags_unadjusted_splits(prices):
    frame = prices.copy()
    frame.iloc[100:, frame.columns.get_loc("Close")] /= 2
    jumps = suspicious_jumps(frame)
    assert list(jumps.index) == [frame.index[100]]
    assert suspicious_jumps(prices).empty
