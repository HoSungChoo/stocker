"""이벤트 전후 ^SOX 수익률.

- ret5   = (기준일 이후 5번째 거래일 종가 / 기준일 이전 5번째 거래일 종가) - 1
- ret_1m = 기준일 종가 대비 21거래일 뒤 수익률, ret_3m = 63거래일 뒤
- 기준일 = 이벤트일 당일(거래일이면) 또는 그 이전 마지막 거래일(휴장이면)
앞뒤 거래일이 모자라면 해당 값은 None(NaN). 이 값은 "그때 얼마나 움직였나"일 뿐 원인 주장이 아니다.
"""

from __future__ import annotations

import pandas as pd
import sqlalchemy as sa

from stocker.db import get_engine, read_event_rows, read_prices, upsert_df

SYMBOL = "^SOX"
DEFAULT_KINDS = ("policy", "war", "election")
WINDOW_5D = 5
WINDOW_1M = 21
WINDOW_3M = 63
IMPACT_COLS = ["ret5", "ret_1m", "ret_3m"]


def _base_position(index: pd.DatetimeIndex, date) -> int | None:
    """date 이하의 마지막 거래일 위치. 첫 거래일보다 이르면 None."""
    pos = int(index.searchsorted(pd.Timestamp(date).normalize(), side="right")) - 1
    return pos if pos >= 0 else None


def _ratio(values, a: int, b: int) -> float:
    """values[b] / values[a] - 1. 위치가 범위를 벗어나면 NaN."""
    if a < 0 or b >= len(values):
        return float("nan")
    return float(values[b] / values[a] - 1.0)


def impact_from_close(close: pd.Series, dates) -> pd.DataFrame:
    """종가 Series와 이벤트 날짜들에서 ret5/ret_1m/ret_3m 계산. 입력 순서대로 (date 인덱스 아님).

    반환 index는 dates와 같은 위치(RangeIndex), 컬럼은 ret5, ret_1m, ret_3m.
    """
    close = close.dropna().sort_index()
    idx = pd.DatetimeIndex(close.index)
    vals = close.to_numpy(dtype="float64")
    nan = float("nan")
    rows = []
    for d in dates:
        i = _base_position(idx, d)
        if i is None:
            rows.append((nan, nan, nan))
            continue
        # 기준일 이후 5번째 거래일은 i+5, 이전 5번째는 i-5
        rows.append(
            (
                _ratio(vals, i - WINDOW_5D, i + WINDOW_5D) if i - WINDOW_5D >= 0 else nan,
                _ratio(vals, i, i + WINDOW_1M),
                _ratio(vals, i, i + WINDOW_3M),
            )
        )
    return pd.DataFrame(rows, columns=IMPACT_COLS)


def path_from_close(close: pd.Series, date, days: int = 30) -> pd.Series:
    """기준일 종가=100으로 정규화한 -days..+days 거래일 경로. 범위 밖 오프셋은 뺀다."""
    close = close.dropna().sort_index()
    i = _base_position(pd.DatetimeIndex(close.index), date)
    if i is None:
        return pd.Series(dtype="float64", index=pd.Index([], name="d", dtype="int64"), name="path")
    lo, hi = max(0, i - days), min(len(close) - 1, i + days)
    seg = close.iloc[lo : hi + 1].to_numpy(dtype="float64") / float(close.iloc[i]) * 100.0
    return pd.Series(seg, index=pd.Index(range(lo - i, hi - i + 1), name="d"), name="path")


def compute_event_impact(
    kinds: tuple[str, ...] | list[str] = DEFAULT_KINDS,
    engine: sa.Engine | None = None,
) -> int:
    """kinds 이벤트의 전후 수익률을 계산해 event_impact에 upsert. 반환: 처리한 행 수."""
    engine = engine or get_engine()
    events = read_event_rows(kinds=list(kinds), engine=engine)
    if events.empty:
        return 0
    close = read_prices(SYMBOL, engine=engine)["close"]
    out = impact_from_close(close, events["date"])
    out.insert(0, "event_id", events["id"].to_numpy())
    out["computed_at"] = pd.Timestamp.now(tz="UTC")
    return upsert_df(out, "event_impact", ["event_id"], engine=engine)


def event_path(event_id: int, days: int = 30, engine: sa.Engine | None = None) -> pd.Series:
    """이벤트 기준일 종가=100인 ^SOX 경로. index=d(거래일 오프셋, -days..+days), name='path'.

    데이터 범위 밖 오프셋은 빠진다. 없는 event_id면 KeyError.
    """
    engine = engine or get_engine()
    ev = read_event_rows(event_id=int(event_id), engine=engine)
    if ev.empty:
        raise KeyError(f"event {event_id} not found")
    date = ev["date"].iloc[0]
    start = date - pd.Timedelta(days=days * 3 + 30)  # 거래일 days개를 덮을 만큼 넉넉히
    end = date + pd.Timedelta(days=days * 3 + 30)
    close = read_prices(SYMBOL, start=start, end=end, engine=engine)["close"]
    return path_from_close(close, date, days)
