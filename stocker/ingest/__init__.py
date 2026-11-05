"""외부 데이터 수집 (yfinance 시세, FRED 매크로)."""

from stocker.ingest.fred import ingest_fred
from stocker.ingest.prices import ingest_prices

__all__ = ["ingest_fred", "ingest_prices"]
