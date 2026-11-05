"""일일 리밸런싱 레버리지 ETF(예: SOXL) NAV 시뮬레이터.

계약: docs/DATA_CONTRACT.md 4절 `stocker.sim.leveraged`.

일일 식 (거래일 수 252 고정):

    nav_t = nav_{t-1} * (1 + L*r_t - (L-1)*(rf_t + spread)/252 - ER/252)

- r_t: 지수 일일 단순수익률. NaN(예: pct_change 첫 행)인 날은 계수 1(변화 없음).
- rf_t: 연율 금융금리(소수). spread: 연율 스프레드(소수). ER: 연율 총보수(소수).
- nav가 0 이하가 되면 0에서 멈추고 이후로도 0이다.

금리 단위 규약 (`rate_scale`):

- FRED `DFF`처럼 퍼센트 단위(5.33 = 5.33%)로 들어오는 값을 받기 위해
  `rate_scale`을 둔다.
  - ``"percent"``: 항상 100으로 나눈다. DFF를 넣을 때는 이것을 명시하라.
  - ``"decimal"``: 그대로 쓴다 (0.0533 = 5.33%).
  - ``"auto"`` (기본): 절댓값 최대가 1.0을 넘으면 퍼센트로 보고 100으로 나눈다.
    주의: 2021년처럼 DFF가 0.1% 미만인 구간만 넣으면 auto는 소수로 오판한다
    (0.08 → 8%). 퍼센트 데이터임을 알면 ``"percent"``를 명시할 것.
- `financing_spread`와 `expense_ratio`는 항상 소수 단위다 (rate_scale 적용 안 함).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

TRADING_DAYS = 252

RateScale = Literal["auto", "percent", "decimal"]


@dataclass(frozen=True)
class CalibrationResult:
    """calibrate 결과. 모든 연율 값은 소수 단위."""

    financing_spread: float  # 최적 스프레드 (연율)
    annual_tracking_error: float  # std(일일 로그수익률 차) * sqrt(252)
    daily_corr: float  # 일일 로그수익률 상관계수 (sim vs actual)
    n_days: int  # 비교에 쓴 일일 수익률 관측 수
    mean_annual_drift: float  # mean(일일 로그수익률 차, sim - actual) * 252


def _naive_index(obj: pd.Series) -> pd.Series:
    """DatetimeIndex로 바꾸고 tz를 제거, 정렬한다."""
    out = obj.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx
    return out.sort_index()


def _scale_rate(values: pd.Series | float, rate_scale: RateScale) -> pd.Series | float:
    if rate_scale == "decimal":
        return values
    if rate_scale == "percent":
        return values / 100.0
    if rate_scale != "auto":
        raise ValueError(f"rate_scale must be auto|percent|decimal, got {rate_scale!r}")
    if isinstance(values, pd.Series):
        mx = values.abs().max()
        return values / 100.0 if pd.notna(mx) and mx > 1.0 else values
    return values / 100.0 if abs(values) > 1.0 else values


def align_financing_rate(
    financing_rate: pd.Series | float,
    index: pd.DatetimeIndex,
    rate_scale: RateScale = "auto",
) -> pd.Series:
    """금융금리를 수익률 인덱스에 맞춘다 (소수 단위 Series 반환).

    Series면 (원본 ∪ 대상) 날짜로 reindex 후 ffill → 대상 날짜만 선택.
    대상 날짜에 관측이 없어도 직전 관측값이 이어진다. 앞쪽 NaN은 0.
    """
    if isinstance(financing_rate, pd.Series):
        rf = _naive_index(financing_rate.astype(float))
        rf = rf[~rf.index.duplicated(keep="last")]
        rf = _scale_rate(rf, rate_scale)
        aligned = rf.reindex(rf.index.union(index)).ffill().reindex(index)
        return aligned.fillna(0.0)
    rate = float(_scale_rate(float(financing_rate), rate_scale))
    return pd.Series(rate, index=index, dtype=float)


def simulate_leveraged(
    index_returns: pd.Series,
    leverage: float = 3.0,
    expense_ratio: float = 0.0075,
    financing_rate: pd.Series | float = 0.0,
    financing_spread: float = 0.0,
    start_value: float = 100.0,
    rate_scale: RateScale = "auto",
) -> pd.Series:
    """지수 일일 단순수익률로 레버리지 ETF NAV 경로를 만든다.

    반환 Series의 인덱스는 index_returns와 같다. 첫 날 값은
    start_value * (첫 날 계수)이며, 첫 수익률이 NaN이면 start_value 그대로다.
    """
    r = _naive_index(index_returns.astype(float))
    rf = align_financing_rate(financing_rate, r.index, rate_scale)
    daily_cost = (leverage - 1.0) * (rf + financing_spread) / TRADING_DAYS
    daily_cost = daily_cost + expense_ratio / TRADING_DAYS
    factor = 1.0 + leverage * r - daily_cost
    factor = factor.where(r.notna(), 1.0)
    # 0 이하 → 0. cumprod이므로 한 번 0이 되면 이후 계속 0.
    factor = factor.clip(lower=0.0)
    nav = start_value * factor.cumprod()
    nav.name = "nav"
    return nav


def _common_window(index_returns: pd.Series, actual_nav: pd.Series) -> tuple[pd.Series, pd.Series]:
    """공통 구간의 수익률(첫 공통일 이후)과 실제 NAV(공통일만)를 돌려준다."""
    r = _naive_index(index_returns.astype(float))
    a = _naive_index(actual_nav.astype(float)).dropna()
    a = a[a > 0]
    common = r.index.intersection(a.index)
    if len(common) < 3:
        raise ValueError("index_returns와 actual_nav의 공통 날짜가 3일 미만이다")
    first, last = common[0], common[-1]
    # 첫 공통일까지의 수익률은 이미 actual에 반영되어 있으므로 NaN(계수 1)으로 둔다.
    # 공통일 사이에 actual이 빠진 날의 지수 수익률은 버리지 않고 복리로 포함된다.
    r_win = r.loc[first:last].copy()
    r_win.iloc[0] = np.nan
    return r_win, a.reindex(common)


def calibrate(
    index_returns: pd.Series,
    actual_nav: pd.Series,
    leverage: float = 3.0,
    expense_ratio: float = 0.0075,
    financing_rate: pd.Series | float = 0.0,
    rate_scale: RateScale = "auto",
    bounds: tuple[float, float] = (-0.20, 0.30),
) -> CalibrationResult:
    """financing_spread를 1차원 최적화해 로그 NAV 평균제곱오차를 최소화한다.

    두 경로는 첫 공통일에 같은 값(실제 NAV 값)에서 시작하도록 정규화한다.
    """
    r_win, actual = _common_window(index_returns, actual_nav)
    start = float(actual.iloc[0])
    log_actual = np.log(actual.to_numpy())
    rf = align_financing_rate(financing_rate, r_win.index, rate_scale)
    tiny = np.finfo(float).tiny

    def sim(spread: float) -> pd.Series:
        nav = simulate_leveraged(
            r_win,
            leverage=leverage,
            expense_ratio=expense_ratio,
            financing_rate=rf,
            financing_spread=spread,
            start_value=start,
            rate_scale="decimal",
        )
        return nav.reindex(actual.index)

    def objective(spread: float) -> float:
        log_sim = np.log(np.maximum(sim(spread).to_numpy(), tiny))
        return float(np.mean((log_sim - log_actual) ** 2))

    opt = minimize_scalar(objective, bounds=bounds, method="bounded", options={"xatol": 1e-8})
    spread = float(opt.x)

    sim_nav = sim(spread)
    sim_lr = np.log(np.maximum(sim_nav, tiny)).diff().iloc[1:]
    act_lr = np.log(actual).diff().iloc[1:]
    diff = sim_lr - act_lr
    return CalibrationResult(
        financing_spread=spread,
        annual_tracking_error=float(diff.std(ddof=1) * np.sqrt(TRADING_DAYS)),
        daily_corr=float(np.corrcoef(sim_lr, act_lr)[0, 1]),
        n_days=int(len(diff)),
        mean_annual_drift=float(diff.mean() * TRADING_DAYS),
    )


def forward_returns(nav: pd.Series, horizons: tuple[int, ...] = (21, 63, 126, 252)) -> pd.DataFrame:
    """각 날짜 기준 h거래일 후 단순수익률. 컬럼 `fwd_{h}`. 끝 h행은 NaN."""
    out = pd.DataFrame(index=nav.index)
    for h in horizons:
        out[f"fwd_{h}"] = nav.shift(-h) / nav - 1.0
    return out
