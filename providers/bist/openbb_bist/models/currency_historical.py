"""BIST Currency Historical Price Model."""

from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.currency_historical import (
    CurrencyHistoricalData,
    CurrencyHistoricalQueryParams,
)
from openbb_core.provider.utils.descriptions import (
    DATA_DESCRIPTIONS,
    QUERY_DESCRIPTIONS,
)
from pydantic import Field

from openbb_bist.utils.constants import INTERVALS, Interval


class BistCurrencyHistoricalQueryParams(CurrencyHistoricalQueryParams):
    """BIST Currency Historical Price Query.

    Pairs are six-letter codes such as USDTRY or EURTRY, the rates used to
    convert TRY prices for a backtest. Source: Yahoo Finance chart API.
    """

    __json_schema_extra__ = {
        "symbol": {"multiple_items_allowed": True},
        "interval": {"choices": INTERVALS},
    }

    interval: Interval = Field(
        default="1d", description=QUERY_DESCRIPTIONS.get("interval", "")
    )


class BistCurrencyHistoricalData(CurrencyHistoricalData):
    """BIST Currency Historical Price Data."""

    symbol: str | None = Field(
        default=None, description=DATA_DESCRIPTIONS.get("symbol", "")
    )


class BistCurrencyHistoricalFetcher(
    Fetcher[BistCurrencyHistoricalQueryParams, list[BistCurrencyHistoricalData]]
):
    """BIST Currency Historical Price Fetcher."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> BistCurrencyHistoricalQueryParams:
        """Transform the query."""
        return BistCurrencyHistoricalQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: BistCurrencyHistoricalQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict]:
        """Extract the data from Yahoo Finance."""
        from openbb_bist.utils import yahoo
        from openbb_bist.utils.symbols import (
            parse_symbols,
            to_currency_pair,
            to_yahoo_currency,
        )

        symbols = parse_symbols(query.symbol, to_yahoo_currency)
        return await yahoo.fetch_history(
            symbols,
            interval=query.interval,
            start_date=query.start_date,
            end_date=query.end_date,
            label=to_currency_pair,
        )

    @staticmethod
    def transform_data(
        query: BistCurrencyHistoricalQueryParams,
        data: list[dict],
        **kwargs: Any,
    ) -> list[BistCurrencyHistoricalData]:
        """Transform the data."""
        return [BistCurrencyHistoricalData.model_validate(row) for row in data]
