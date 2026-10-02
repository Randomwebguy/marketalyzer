import asyncio
from datetime import date, datetime, timedelta

import pytest
from conftest import IST, THYAO_DAILY, make_chart, not_found, ts
from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.utils.errors import EmptyDataError

from openbb_bist.utils import yahoo

TODAY = date(2026, 10, 2)


class TestResolveWindow:
    def test_daily_defaults_to_one_year(self):
        assert yahoo.resolve_window("1d", None, None, today=TODAY) == (
            TODAY - timedelta(days=365),
            TODAY,
        )

    def test_intraday_defaults_to_one_week(self):
        assert yahoo.resolve_window("5m", None, None, today=TODAY) == (
            TODAY - timedelta(days=7),
            TODAY,
        )

    def test_intraday_start_is_clamped(self):
        with pytest.warns(UserWarning, match="start_date moved"):
            start, end = yahoo.resolve_window("1m", date(2026, 1, 1), None, today=TODAY)
        assert (start, end) == (TODAY - timedelta(days=29), TODAY)

    def test_intraday_end_too_old(self):
        with pytest.raises(OpenBBError, match="too old"):
            yahoo.resolve_window("5m", date(2025, 1, 1), date(2025, 1, 5), TODAY)

    def test_start_after_end(self):
        with pytest.raises(OpenBBError, match="must not be after"):
            yahoo.resolve_window("1d", date(2026, 10, 2), date(2026, 10, 1), TODAY)


def test_split_window_chunks_one_minute_requests():
    assert yahoo.split_window(date(2026, 9, 1), date(2026, 9, 20), "1m") == [
        (date(2026, 9, 1), date(2026, 9, 6)),
        (date(2026, 9, 7), date(2026, 9, 12)),
        (date(2026, 9, 13), date(2026, 9, 18)),
        (date(2026, 9, 19), date(2026, 9, 20)),
    ]


def test_split_window_keeps_other_intervals_whole():
    window = (date(2025, 1, 1), date(2026, 10, 2))
    assert yahoo.split_window(*window, "1d") == [window]


def test_chart_params_cover_whole_istanbul_days():
    params = yahoo.chart_params("1h", date(2026, 9, 28), date(2026, 10, 2))
    assert params["period1"] == str(ts(2026, 9, 28, 0))
    assert params["period2"] == str(ts(2026, 10, 3, 0))
    assert params["interval"] == "60m"
    assert yahoo.chart_params("1W", TODAY, TODAY)["interval"] == "1wk"


class TestChartResult:
    def test_returns_result(self):
        payload = make_chart("THYAO.IS", THYAO_DAILY)
        assert yahoo.chart_result(payload, "THYAO")["meta"]["symbol"] == "THYAO.IS"

    def test_raises_yahoo_error(self):
        with pytest.raises(EmptyDataError, match="FOO: No data found"):
            yahoo.chart_result(not_found(), "FOO")

    def test_raises_on_empty_payload(self):
        with pytest.raises(EmptyDataError, match="No data returned"):
            yahoo.chart_result({}, "FOO")


class TestChartRecords:
    def result(self, rows=THYAO_DAILY, **kwargs):
        return make_chart("THYAO.IS", rows, **kwargs)["chart"]["result"][0]

    def test_daily_rows(self):
        records = yahoo.chart_records(self.result(), "1d")
        assert [row["date"] for row in records] == [
            date(2026, 9, 28),
            date(2026, 9, 29),
            date(2026, 10, 1),
            date(2026, 10, 2),
        ]
        assert records[0] == {
            "date": date(2026, 9, 28),
            "open": 300.0,
            "high": 305.5,
            "low": 298.25,
            "close": 304.0,
            "volume": 21_000_000,
        }
        assert records[-1]["close"] == 306.0

    def test_running_session_bar_replaces_earlier_bar(self):
        rows = [*THYAO_DAILY, (ts(2026, 10, 2, 15, 30), 309.0, 312.0, 305.0, 311.5, 2)]
        records = yahoo.chart_records(self.result(rows), "1d")
        assert len(records) == 4
        assert records[-1]["close"] == 311.5

    def test_intraday_rows_keep_istanbul_time(self):
        rows = [
            (ts(2026, 10, 2, 10, 0), 309.0, 309.5, 308.5, 309.25, 1000),
            (ts(2026, 10, 2, 10, 5), 309.25, 310.0, 309.0, 309.75, 1200),
        ]
        records = yahoo.chart_records(self.result(rows), "5m")
        assert records[0]["date"] == datetime(2026, 10, 2, 10, 0, tzinfo=IST)
        assert records[0]["date"].utcoffset() == timedelta(hours=3)

    def test_dividend_adjustment_scales_every_price(self):
        rows = THYAO_DAILY[:2]
        result = self.result(rows, adjclose=[288.8, 302.5])
        records = yahoo.chart_records(result, "1d", "splits_and_dividends")
        assert records[0]["close"] == 288.8
        assert records[0]["open"] == round(300.0 * 288.8 / 304.0, 4)
        assert records[1]["close"] == 302.5

    def test_actions_attach_to_their_bar(self):
        events = {
            "dividends": {
                "a": {"amount": 3.25, "date": ts(2026, 10, 1, 7)},
            },
            "splits": {
                "b": {"date": ts(2026, 9, 29, 7), "numerator": 2, "denominator": 1},
            },
        }
        records = yahoo.chart_records(
            self.result(events=events), "1d", include_actions=True
        )
        by_date = {row["date"]: row for row in records}
        assert by_date[date(2026, 10, 1)]["dividend"] == 3.25
        assert by_date[date(2026, 9, 28)]["dividend"] == 0.0
        assert by_date[date(2026, 9, 29)]["split_ratio"] == 2.0
        assert by_date[date(2026, 10, 2)]["split_ratio"] is None

    def test_weekly_actions_attach_to_the_week_bar(self):
        rows = [
            (ts(2026, 9, 21), 290.0, 300.0, 288.0, 299.0, 90_000_000),
            (ts(2026, 9, 28), 300.0, 311.0, 298.25, 306.0, 83_500_000),
        ]
        events = {"dividends": {"a": {"amount": 3.25, "date": ts(2026, 10, 1, 7)}}}
        records = yahoo.chart_records(
            self.result(rows, events=events), "1W", include_actions=True
        )
        assert [row["dividend"] for row in records] == [0.0, 3.25]


class TestFetchHistory:
    def test_single_symbol(self, fake_chart):
        fake_chart.responses["THYAO.IS"] = make_chart("THYAO.IS", THYAO_DAILY)
        data = asyncio.run(
            yahoo.fetch_history(
                ["THYAO.IS"], "1d", date(2026, 9, 28), date(2026, 10, 2)
            )
        )
        assert len(data) == 4
        assert "symbol" not in data[0]
        symbol, params = fake_chart.calls[0]
        assert symbol == "THYAO.IS"
        assert params["interval"] == "1d"

    def test_single_unknown_symbol_raises(self, fake_chart):
        with pytest.raises(EmptyDataError, match="NOPE: No data found"):
            asyncio.run(yahoo.fetch_history(["NOPE.IS"], "1d", None, None))

    def test_multiple_symbols_warn_on_failure(self, fake_chart):
        fake_chart.responses["THYAO.IS"] = make_chart("THYAO.IS", THYAO_DAILY)
        fake_chart.responses["GARAN.IS"] = make_chart(
            "GARAN.IS", [(ts(2026, 9, 28), 130.0, 131.0, 129.0, 130.5, 50_000_000)]
        )
        with pytest.warns(UserWarning, match="NOPE"):
            data = asyncio.run(
                yahoo.fetch_history(
                    ["THYAO.IS", "GARAN.IS", "NOPE.IS"],
                    "1d",
                    date(2026, 9, 28),
                    date(2026, 10, 2),
                )
            )
        assert [(row["date"], row["symbol"]) for row in data[:2]] == [
            (date(2026, 9, 28), "GARAN"),
            (date(2026, 9, 28), "THYAO"),
        ]
        assert len(data) == 5

    def test_one_minute_bars_are_fetched_in_chunks(self, fake_chart):
        today = datetime.now(IST).date()
        start = today - timedelta(days=20)
        empty_chunk_start = str(
            int(
                datetime.combine(
                    start + timedelta(days=6), datetime.min.time(), IST
                ).timestamp()
            )
        )

        def respond(params):
            if params["period1"] == empty_chunk_start:
                return not_found()
            opened = int(params["period1"]) + 10 * 3600
            return make_chart("THYAO.IS", [(opened, 1.0, 1.0, 1.0, 1.0, 1)])

        fake_chart.responses["THYAO.IS"] = respond
        data = asyncio.run(yahoo.fetch_history(["THYAO.IS"], "1m", start, today))
        assert len(fake_chart.calls) == 4
        assert len(data) == 3

    def test_intraday_actions_are_dropped(self, fake_chart):
        today = datetime.now(IST).date()
        fake_chart.responses["THYAO.IS"] = make_chart(
            "THYAO.IS",
            [(ts(today.year, today.month, today.day), 1.0, 1.0, 1.0, 1.0, 1)],
        )
        with pytest.warns(UserWarning, match="daily or longer"):
            data = asyncio.run(
                yahoo.fetch_history(
                    ["THYAO.IS"], "5m", today, today, include_actions=True
                )
            )
        assert "dividend" not in data[0]


QUOTE_META = {
    "regularMarketPrice": 306.0,
    "regularMarketTime": ts(2026, 10, 2, 17, 45),
    "previousClose": 309.75,
    "regularMarketDayHigh": 311.0,
    "regularMarketDayLow": 305.0,
    "regularMarketVolume": 19_000_000,
    "fiftyTwoWeekHigh": 340.0,
    "fiftyTwoWeekLow": 255.5,
    "longName": "Türk Hava Yollari Anonim Ortakligi",
}


def test_quote_record():
    payload = make_chart(
        "THYAO.IS",
        [(ts(2026, 10, 2), 309.0, 311.0, 305.0, 306.0, 19_000_000)],
        **QUOTE_META,
    )
    record = yahoo.quote_record(payload["chart"]["result"][0], "THYAO.IS")
    assert record["symbol"] == "THYAO"
    assert record["last_price"] == 306.0
    assert record["prev_close"] == 309.75
    assert record["change"] == -3.75
    assert record["change_percent"] == round(-3.75 / 309.75, 6)
    assert record["open"] == 309.0
    assert record["last_timestamp"] == datetime(2026, 10, 2, 17, 45, tzinfo=IST)
    assert record["currency"] == "TRY"
    assert record["exchange"] == "Istanbul"


def test_quote_record_without_previous_close():
    payload = make_chart("THYAO.IS", [], regularMarketPrice=306.0)
    record = yahoo.quote_record(payload["chart"]["result"][0], "THYAO.IS")
    assert record["change"] is None
    assert record["change_percent"] is None


def test_fetch_quotes_skips_failures(fake_chart):
    fake_chart.responses["THYAO.IS"] = make_chart("THYAO.IS", [], **QUOTE_META)
    with pytest.warns(UserWarning, match="NOPE"):
        data = asyncio.run(yahoo.fetch_quotes(["THYAO.IS", "NOPE.IS"]))
    assert [row["symbol"] for row in data] == ["THYAO"]
    assert fake_chart.calls[0][1] == {"range": "1d", "interval": "1d"}
