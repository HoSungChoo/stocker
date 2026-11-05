"""특징 생성. 모두 순수 함수(DataFrame/Series in → DataFrame out).

룩어헤드 금지 원칙: 어떤 날짜 t의 행도 t 이하의 데이터만으로 계산된다.
- 가격 창 벡터는 창 안의 값만 쓴다(창 내부 z-score).
- 전고점은 t까지의 최고가(expanding max).
- 매크로·사이클의 z-score는 expanding 평균·표준편차.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

SHAPE_PREFIX = "shape_"
DRAWDOWN_COL = "drawdown"
DD_DAYS_COL = "dd_days"
WSTS_METRIC = "wsts_sales_world_usd_m"
MU_GM_METRIC = "mu_gross_margin"


def _clean_series(s: pd.Series) -> pd.Series:
    """float, tz-naive DatetimeIndex, 정렬, 중복 제거, NaN 제거."""
    out = pd.Series(s, copy=True).astype("float64")
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out.dropna()


def shape_columns(window: int) -> list[str]:
    return [f"{SHAPE_PREFIX}{i}" for i in range(window)]


def build_price_features(close: pd.Series, window: int) -> pd.DataFrame:
    """각 끝 날짜 t에 대한 가격 형태 특징.

    index: 창이 완전한(관측 `window`개) 끝 날짜 t.
    columns:
      shape_0..shape_{window-1}  창 시작 대비 누적 로그수익률을 창 내부에서 z-score한 벡터
      drawdown                   t 종가 / (t까지의 최고 종가) - 1  (0 이하)
      dd_days                    최고가가 찍힌 날 이후 지난 거래일 수 (신고가면 0)
    """
    s = _clean_series(close)
    if (s <= 0).any():
        raise ValueError("close에는 0 이하의 값이 없어야 한다")
    n = len(s)
    if n < window:
        return pd.DataFrame(columns=[*shape_columns(window), DRAWDOWN_COL, DD_DAYS_COL])

    log_px = np.log(s.to_numpy())
    # (n-window+1, window): 각 행이 하나의 창. 창 시작 대비 누적 로그수익률.
    win = sliding_window_view(log_px, window)
    cum = win - win[:, :1]
    mean = cum.mean(axis=1, keepdims=True)
    std = cum.std(axis=1, keepdims=True)
    std = np.where(std > 0, std, 1.0)  # 완전히 평평한 창은 0 벡터
    shape = (cum - mean) / std

    # 전고점(expanding) 대비 낙폭과 지속일. t 이하 데이터만 쓴다.
    px = s.to_numpy()
    running_max = np.maximum.accumulate(px)
    drawdown = px / running_max - 1.0
    pos = np.arange(n)
    high_pos = np.maximum.accumulate(np.where(px >= running_max, pos, 0))
    dd_days = pos - high_pos

    ends = s.index[window - 1 :]
    out = pd.DataFrame(shape, index=ends, columns=shape_columns(window))
    out[DRAWDOWN_COL] = drawdown[window - 1 :]
    out[DD_DAYS_COL] = dd_days[window - 1 :].astype("int64")
    out.index.name = "end_date"
    return out


def expanding_zscore(s: pd.Series, min_periods: int = 60) -> pd.Series:
    """(x_t - mean(x_{<=t})) / std(x_{<=t}). 관측이 min_periods 미만이면 NaN."""
    mean = s.expanding(min_periods=min_periods).mean()
    std = s.expanding(min_periods=min_periods).std(ddof=1)
    z = (s - mean) / std
    return z.where(std > 0)


def build_macro_features(
    series: dict[str, pd.Series], change_days: int, min_periods: int = 60
) -> pd.DataFrame:
    """시리즈별 2개 특징: 수준의 expanding z-score, `change_days`(영업일) 변화량의 expanding
    z-score.

    서로 빈도가 다른 시리즈(일/영업일/월)를 영업일 격자에 ffill로 올린 뒤 계산한다.
    index: 영업일 격자(전 시리즈 최소~최대 날짜). columns: `{id}_level_z`, `{id}_chg_z`.
    """
    cleaned = {k: _clean_series(v) for k, v in series.items()}
    cleaned = {k: v for k, v in cleaned.items() if not v.empty}
    if not cleaned:
        return pd.DataFrame()
    start = min(v.index[0] for v in cleaned.values())
    end = max(v.index[-1] for v in cleaned.values())
    grid = pd.bdate_range(start, end)
    cols: dict[str, pd.Series] = {}
    for sid, s in cleaned.items():
        on_grid = s.reindex(s.index.union(grid)).ffill().reindex(grid)
        cols[f"{sid}_level_z"] = expanding_zscore(on_grid, min_periods)
        change = on_grid - on_grid.shift(change_days)
        cols[f"{sid}_chg_z"] = expanding_zscore(change, min_periods)
    out = pd.DataFrame(cols, index=grid)
    out.index.name = "date"
    return out


def build_cycle_features(series: dict[str, pd.Series]) -> pd.DataFrame:
    """산업 사이클 특징(월말 격자).

    columns:
      wsts_yoy       WSTS 전세계 매출의 전년 동월 대비 변화율(%)
      wsts_yoy_chg6  wsts_yoy의 6개월 전 대비 변화(%p)
      mu_gross_margin  (있으면) 마이크론 분기 매출총이익률, 월말로 ffill
    표준화(거리 계산용)는 engine에서 후보 집합 통계로 한다.
    """
    cleaned = {k: _clean_series(v) for k, v in series.items()}
    cleaned = {k: v for k, v in cleaned.items() if not v.empty}
    cols: dict[str, pd.Series] = {}
    if WSTS_METRIC in cleaned:
        monthly = cleaned[WSTS_METRIC].resample("ME").last()
        yoy = (monthly / monthly.shift(12) - 1.0) * 100.0
        cols["wsts_yoy"] = yoy
        cols["wsts_yoy_chg6"] = yoy - yoy.shift(6)
    if MU_GM_METRIC in cleaned:
        gm = cleaned[MU_GM_METRIC].resample("ME").last().ffill()
        cols[MU_GM_METRIC] = gm
    if not cols:
        return pd.DataFrame()
    out = pd.DataFrame(cols)
    out = out.dropna(how="all")
    out.index.name = "date"
    return out
