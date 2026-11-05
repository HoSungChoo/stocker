"""FRED 매크로 시계열 수집. long format DataFrame을 만들고 macro_series에 upsert."""

from __future__ import annotations

import pandas as pd
from fredapi import Fred

from stocker.config import settings

DEFAULT_SERIES = [
    "DFF",
    "DGS2",
    "DGS10",
    "T10Y2Y",
    "DCOILWTICO",
    "DTWEXBGS",
    "VIXCLS",
    "CPIAUCSL",
    "M2SL",  # 월간 M2 (돈의 가치)
    "WALCL",  # 주간 연준 자산, 2002-12~
    "BAMLH0A0HYM2",  # 하이일드 OAS(%p), 신용 스트레스, 1996-12~ 일간
    "BAMLH0A3HYC",  # CCC 이하 OAS(%p), 가장 위험한 회사채의 가산금리, 1996-12~ 일간
    "DGS3MO",  # 3개월물 국채(%), 금리 방향 보조, 1981~ 일간
]

COLUMNS = ["series_id", "date", "value", "source"]


def fetch_fred(series_ids: list[str], start="1990-01-01", end=None) -> pd.DataFrame:
    """long format: series_id,date,value,source. 결측은 행 생략. 원본 빈도 유지."""
    key = settings.fred_key
    if key is None:
        raise RuntimeError(
            "FRED_API_KEY가 설정되지 않았다. .env에 FRED_API_KEY=<키>를 넣어라 "
            "(https://fred.stlouisfed.org/docs/api/api_key.html)."
        )
    fred = Fred(api_key=key)
    frames = []
    for sid in series_ids:
        s = fred.get_series(sid, observation_start=start, observation_end=end)
        s = pd.Series(s).dropna().astype("float64")
        if s.empty:
            continue
        idx = pd.DatetimeIndex(s.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        frames.append(
            pd.DataFrame(
                {"series_id": sid, "date": idx.normalize(), "value": s.to_numpy(), "source": "fred"}
            )
        )
    if not frames:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})
    return pd.concat(frames, ignore_index=True)[COLUMNS]


def store_fred(df: pd.DataFrame, engine=None) -> int:
    """fetch_fred 결과를 macro_series에 upsert. 영향 행 수 반환."""
    from stocker.db import upsert_df  # 지연 import

    return upsert_df(df, "macro_series", pk=["series_id", "date"], engine=engine)


def ingest_fred(series_ids=DEFAULT_SERIES, start="1990-01-01", engine=None) -> int:
    return store_fred(fetch_fred(list(series_ids), start=start), engine=engine)
