"""Live smoke test: fetch real data through every fetcher.

Needs internet access to query1.finance.yahoo.com. Run it after installing the
package to confirm the Yahoo endpoint still answers in the expected format:

    python scripts/smoke_test.py
"""

import asyncio
import sys
from datetime import date, timedelta

from openbb_bist.models.available_indices import BistAvailableIndicesFetcher
from openbb_bist.models.currency_historical import BistCurrencyHistoricalFetcher
from openbb_bist.models.equity_historical import BistEquityHistoricalFetcher
from openbb_bist.models.equity_quote import BistEquityQuoteFetcher
from openbb_bist.models.index_historical import BistIndexHistoricalFetcher

START = date.today() - timedelta(days=14)

CHECKS = [
    (
        "THYAO,GARAN daily",
        BistEquityHistoricalFetcher,
        {"symbol": "THYAO,GARAN", "start_date": START},
    ),
    ("THYAO 5m", BistEquityHistoricalFetcher, {"symbol": "THYAO", "interval": "5m"}),
    (
        "XU100 daily",
        BistIndexHistoricalFetcher,
        {"symbol": "XU100", "start_date": START},
    ),
    (
        "USDTRY daily",
        BistCurrencyHistoricalFetcher,
        {"symbol": "USDTRY", "start_date": START},
    ),
    ("THYAO quote", BistEquityQuoteFetcher, {"symbol": "THYAO"}),
    ("index list", BistAvailableIndicesFetcher, {}),
]


def main() -> int:
    failures = 0
    for label, fetcher, params in CHECKS:
        try:
            rows = asyncio.run(fetcher.fetch_data(params))
        except Exception as error:
            failures += 1
            print(f"FAIL  {label}: {type(error).__name__}: {error}")
            continue
        print(f"OK    {label}: {len(rows)} rows, last = {rows[-1].model_dump()}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
