import asyncio
from datetime import date

import pytest
from conftest import THYAO_DAILY, make_chart, ts

from openbb_bist import bist_provider
from openbb_bist.models.available_indices import BistAvailableIndicesFetcher
from openbb_bist.models.currency_historical import BistCurrencyHistoricalFetcher
from openbb_bist.models.equity_historical import BistEquityHistoricalFetcher
from openbb_bist.models.equity_quote import BistEquityQuoteFetcher
from openbb_bist.models.index_historical import BistIndexHistoricalFetcher

WEEK = {"start_date": date(2026, 9, 28), "end_date": date(2026, 10, 2)}


@pytest.fixture
def market(fake_chart):
    fake_chart.responses["THYAO.IS"] = make_chart(
        "THYAO.IS",
        THYAO_DAILY,
        regularMarketPrice=306.0,
        previousClose=309.75,
        regularMarketTime=ts(2026, 10, 2, 17, 45),
    )
    fake_chart.responses["GARAN.IS"] = make_chart(
        "GARAN.IS", [(ts(2026, 9, 28), 130.0, 131.0, 129.0, 130.5, 50_000_000)]
    )
    fake_chart.responses["XU100.IS"] = make_chart(
        "XU100.IS",
        [(ts(2026, 9, 28), 10_400.0, 10_480.5, 10_350.25, 10_455.75, 0)],
        instrumentType="INDEX",
    )
    for pair, rate in (("USDTRY=X", 41.2), ("EURTRY=X", 48.3)):
        fake_chart.responses[pair] = make_chart(
            pair, [(ts(2026, 9, 28, 1), rate, rate, rate, rate, 0)], currency="TRY"
        )
    return fake_chart


def test_provider_registers_every_fetcher():
    assert bist_provider.name == "bist"
    assert bist_provider.credentials == []
    assert bist_provider.fetcher_dict == {
        "AvailableIndices": BistAvailableIndicesFetcher,
        "CurrencyHistorical": BistCurrencyHistoricalFetcher,
        "EquityHistorical": BistEquityHistoricalFetcher,
        "EquityQuote": BistEquityQuoteFetcher,
        "IndexHistorical": BistIndexHistoricalFetcher,
    }


@pytest.mark.parametrize(
    ("fetcher", "params"),
    [
        (BistEquityHistoricalFetcher, {"symbol": "THYAO", **WEEK}),
        (BistEquityHistoricalFetcher, {"symbol": "THYAO,GARAN", **WEEK}),
        (BistIndexHistoricalFetcher, {"symbol": "XU100", **WEEK}),
        (BistCurrencyHistoricalFetcher, {"symbol": "USDTRY", **WEEK}),
        (BistEquityQuoteFetcher, {"symbol": "THYAO"}),
        (BistAvailableIndicesFetcher, {}),
    ],
)
def test_fetcher_contract(market, fetcher, params):
    fetcher.test(params)


def test_equity_historical(market):
    rows = asyncio.run(
        BistEquityHistoricalFetcher.fetch_data({"symbol": "thyao.is", **WEEK})
    )
    assert market.calls[0][0] == "THYAO.IS"
    assert [row.close for row in rows] == [304.0, 302.5, 309.75, 306.0]
    assert rows[0].date == date(2026, 9, 28)
    assert rows[0].symbol is None


def test_equity_historical_multiple_symbols(market):
    rows = asyncio.run(
        BistEquityHistoricalFetcher.fetch_data({"symbol": "THYAO,GARAN", **WEEK})
    )
    assert {row.symbol for row in rows} == {"THYAO", "GARAN"}


def test_index_historical_alias(market):
    rows = asyncio.run(
        BistIndexHistoricalFetcher.fetch_data({"symbol": "BIST100", **WEEK})
    )
    assert market.calls[0][0] == "XU100.IS"
    assert rows[0].close == 10_455.75


def test_currency_historical(market):
    rows = asyncio.run(
        BistCurrencyHistoricalFetcher.fetch_data({"symbol": "USDTRY,EURTRY", **WEEK})
    )
    assert {(row.symbol, row.close) for row in rows} == {
        ("USDTRY", 41.2),
        ("EURTRY", 48.3),
    }


def test_equity_quote(market):
    (quote,) = asyncio.run(BistEquityQuoteFetcher.fetch_data({"symbol": "THYAO"}))
    assert quote.symbol == "THYAO"
    assert quote.last_price == 306.0
    assert quote.change == -3.75
    assert quote.currency == "TRY"


def test_available_indices():
    rows = asyncio.run(BistAvailableIndicesFetcher.fetch_data({}))
    codes = {row.symbol for row in rows}
    assert {"XU100", "XU030", "XBANK"} <= codes
    assert all(row.currency == "TRY" for row in rows)
