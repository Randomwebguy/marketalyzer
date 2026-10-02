"""Borsa Istanbul (BIST) provider for the OpenBB Open Data Platform."""

from openbb_core.provider.abstract.provider import Provider

from openbb_bist.models.available_indices import BistAvailableIndicesFetcher
from openbb_bist.models.currency_historical import BistCurrencyHistoricalFetcher
from openbb_bist.models.equity_historical import BistEquityHistoricalFetcher
from openbb_bist.models.equity_quote import BistEquityQuoteFetcher
from openbb_bist.models.index_historical import BistIndexHistoricalFetcher

bist_provider = Provider(
    name="bist",
    website="https://www.borsaistanbul.com",
    description="""Borsa Istanbul stocks, indices and TRY exchange rates.

Symbols are BIST codes (THYAO, GARAN, XU100); the Yahoo ".IS" suffix is added
automatically. Prices come from the Yahoo Finance chart API and are delayed
about 15 minutes. No API key is required.""",
    fetcher_dict={
        "AvailableIndices": BistAvailableIndicesFetcher,
        "CurrencyHistorical": BistCurrencyHistoricalFetcher,
        "EquityHistorical": BistEquityHistoricalFetcher,
        "EquityQuote": BistEquityQuoteFetcher,
        "IndexHistorical": BistIndexHistoricalFetcher,
    },
    repr_name="Borsa Istanbul",
)
