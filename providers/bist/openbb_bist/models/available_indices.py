"""BIST Available Indices Model."""

from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.available_indices import (
    AvailableIndicesData,
    AvailableIndicesQueryParams,
)

from openbb_bist.utils.constants import BIST_INDICES


class BistAvailableIndicesQueryParams(AvailableIndicesQueryParams):
    """BIST Available Indices Query."""


class BistAvailableIndicesData(AvailableIndicesData):
    """BIST Available Indices Data."""


class BistAvailableIndicesFetcher(
    Fetcher[BistAvailableIndicesQueryParams, list[BistAvailableIndicesData]]
):
    """BIST Available Indices Fetcher, served from a static list."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> BistAvailableIndicesQueryParams:
        """Transform the query."""
        return BistAvailableIndicesQueryParams(**params)

    @staticmethod
    def extract_data(
        query: BistAvailableIndicesQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict]:
        """Return the static list of Borsa Istanbul indices."""
        return [
            {"symbol": code, "name": name, "exchange": "XIST", "currency": "TRY"}
            for code, name in BIST_INDICES.items()
        ]

    @staticmethod
    def transform_data(
        query: BistAvailableIndicesQueryParams,
        data: list[dict],
        **kwargs: Any,
    ) -> list[BistAvailableIndicesData]:
        """Transform the data."""
        return [BistAvailableIndicesData.model_validate(row) for row in data]
