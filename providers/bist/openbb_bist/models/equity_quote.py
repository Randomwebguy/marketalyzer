"""BIST Equity Quote Model."""

from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.equity_quote import (
    EquityQuoteData,
    EquityQuoteQueryParams,
)
from pydantic import Field


class BistEquityQuoteQueryParams(EquityQuoteQueryParams):
    """BIST Equity Quote Query.

    Accepts stock and index codes. Prices are delayed about 15 minutes, so a
    paper-trading loop built on them trades on delayed data.
    """

    __json_schema_extra__ = {"symbol": {"multiple_items_allowed": True}}


class BistEquityQuoteData(EquityQuoteData):
    """BIST Equity Quote Data."""

    currency: str | None = Field(default=None, description="Trading currency.")


class BistEquityQuoteFetcher(
    Fetcher[BistEquityQuoteQueryParams, list[BistEquityQuoteData]]
):
    """BIST Equity Quote Fetcher."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> BistEquityQuoteQueryParams:
        """Transform the query."""
        return BistEquityQuoteQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: BistEquityQuoteQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict]:
        """Extract the data from Yahoo Finance."""
        from openbb_bist.utils import yahoo
        from openbb_bist.utils.symbols import parse_symbols, to_yahoo_symbol

        symbols = parse_symbols(query.symbol, to_yahoo_symbol)
        return await yahoo.fetch_quotes(symbols)

    @staticmethod
    def transform_data(
        query: BistEquityQuoteQueryParams,
        data: list[dict],
        **kwargs: Any,
    ) -> list[BistEquityQuoteData]:
        """Transform the data."""
        return [BistEquityQuoteData.model_validate(row) for row in data]
