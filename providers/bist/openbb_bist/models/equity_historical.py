"""BIST Equity Historical Price Model."""

from typing import Any, Literal

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.equity_historical import (
    EquityHistoricalData,
    EquityHistoricalQueryParams,
)
from openbb_core.provider.utils.descriptions import (
    DATA_DESCRIPTIONS,
    QUERY_DESCRIPTIONS,
)
from pydantic import Field

from openbb_bist.utils.constants import INTERVALS, Interval


class BistEquityHistoricalQueryParams(EquityHistoricalQueryParams):
    """BIST Equity Historical Price Query.

    Symbols are BIST codes such as THYAO or GARAN; the ".IS" suffix is optional.
    Source: Yahoo Finance chart API, delayed about 15 minutes.
    """

    __json_schema_extra__ = {
        "symbol": {"multiple_items_allowed": True},
        "interval": {"choices": INTERVALS},
    }

    interval: Interval = Field(
        default="1d",
        description=QUERY_DESCRIPTIONS.get("interval", "")
        + " Intraday bars reach back 30 days for 1m, 60 days for 2m-30m and"
        + " 730 days for 1h.",
    )
    adjustment: Literal["splits_only", "splits_and_dividends"] = Field(
        default="splits_only",
        description="Price adjustment. Yahoo prices are split-adjusted, which on"
        + " BIST includes bonus issues (bedelsiz); 'splits_and_dividends' also"
        + " back-adjusts for cash dividends.",
    )
    include_actions: bool = Field(
        default=False,
        description="Add dividend and split_ratio columns (daily or longer bars).",
    )


class BistEquityHistoricalData(EquityHistoricalData):
    """BIST Equity Historical Price Data."""

    symbol: str | None = Field(
        default=None, description=DATA_DESCRIPTIONS.get("symbol", "")
    )
    dividend: float | None = Field(
        default=None, description="Cash dividend per share paid on this bar."
    )
    split_ratio: float | None = Field(
        default=None, description="Split ratio effective on this bar."
    )


class BistEquityHistoricalFetcher(
    Fetcher[BistEquityHistoricalQueryParams, list[BistEquityHistoricalData]]
):
    """BIST Equity Historical Price Fetcher."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> BistEquityHistoricalQueryParams:
        """Transform the query."""
        return BistEquityHistoricalQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: BistEquityHistoricalQueryParams,
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
            adjustment=query.adjustment,
            include_actions=query.include_actions,
        )

    @staticmethod
    def transform_data(
        query: BistEquityHistoricalQueryParams,
        data: list[dict],
        **kwargs: Any,
    ) -> list[BistEquityHistoricalData]:
        """Transform the data."""
        return [BistEquityHistoricalData.model_validate(row) for row in data]
