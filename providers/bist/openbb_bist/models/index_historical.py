"""BIST Index Historical Model."""

from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.index_historical import (
    IndexHistoricalData,
    IndexHistoricalQueryParams,
)
from openbb_core.provider.utils.descriptions import QUERY_DESCRIPTIONS
from pydantic import Field

from openbb_bist.utils.constants import INTERVALS, Interval


class BistIndexHistoricalQueryParams(IndexHistoricalQueryParams):
    """BIST Index Historical Query.

    Symbols are BIST index codes such as XU100, XU030 or XBANK; BIST100 and
    BIST30 are accepted as aliases. Source: Yahoo Finance chart API.
    """

    __json_schema_extra__ = {
        "symbol": {"multiple_items_allowed": True},
        "interval": {"choices": INTERVALS},
    }

    interval: Interval = Field(
        default="1d", description=QUERY_DESCRIPTIONS.get("interval", "")
    )


class BistIndexHistoricalData(IndexHistoricalData):
    """BIST Index Historical Data."""


class BistIndexHistoricalFetcher(
    Fetcher[BistIndexHistoricalQueryParams, list[BistIndexHistoricalData]]
):
    """BIST Index Historical Fetcher."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> BistIndexHistoricalQueryParams:
        """Transform the query."""
        return BistIndexHistoricalQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: BistIndexHistoricalQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict]:
        """Extract the data from Yahoo Finance."""
        from openbb_bist.utils import yahoo
        from openbb_bist.utils.symbols import parse_symbols, to_yahoo_symbol

        symbols = parse_symbols(query.symbol, to_yahoo_symbol)
        return await yahoo.fetch_history(
            symbols,
            interval=query.interval,
            start_date=query.start_date,
            end_date=query.end_date,
        )

    @staticmethod
    def transform_data(
        query: BistIndexHistoricalQueryParams,
        data: list[dict],
        **kwargs: Any,
    ) -> list[BistIndexHistoricalData]:
        """Transform the data."""
        return [BistIndexHistoricalData.model_validate(row) for row in data]
