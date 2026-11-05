"""유사도 엔진 설정. 계약: docs/DATA_CONTRACT.md 8.2, PHASE6_CONTRACT.md 23·24절."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DEFAULT_WEIGHTS = {"price_shape": 0.5, "macro": 0.3, "semi_cycle": 0.2}
DEFAULT_MACRO_SERIES = ("DFF", "DGS10", "T10Y2Y", "DCOILWTICO", "DTWEXBGS", "VIXCLS")
CYCLE_METRICS = ("wsts_sales_world_usd_m", "mu_gross_margin")

# 환경 우선 모드(PLAN_PHASE6 3절)가 추가로 쓰는 FRED 시리즈. 모양 우선 모드의 매크로 레이어에는
# 들어가지 않는다(기존 결과를 바꾸지 않기 위해). 실질금리 = DFF - CPI YoY 는 엔진에서 파생.
REGIME_EXTRA_SERIES = ("CPIAUCSL", "M2SL", "WALCL")
# 환경 우선 모드 그룹 가중치 (PLAN_PHASE6 3절, 고정. 백테스트 결과를 보고 바꾸지 않는다)
REGIME_WEIGHTS = {
    "position": 0.30,  # 위치: 낙폭, 고점 이후 일수, 1·2년 수익률, 60일 변동성
    # 돈의 가치: 기준금리(z·수준), 6개월 변화, 실질금리, 10년물, 스프레드, 달러, M2, 연준자산
    "money": 0.25,
    "policy": 0.10,  # 정책·이벤트: 금리 방향(-1/0/+1), 6개월 내 정책·전쟁 이벤트(0/1)
    "cycle": 0.15,  # 업황: WSTS YoY, YoY 6개월 변화
    "narrative": 0.10,  # 내러티브 대리: VIX, 200일선 괴리
    "shape": 0.10,  # 모양: 기존 DTW 점수
}
# 정책·이벤트 그룹에 넣는 이벤트 종류 (events.kind)
REGIME_EVENT_KINDS = ("policy", "war")

Mode = Literal["shape", "regime"]


@dataclass
class SimilarityConfig:
    window: int = 120  # 거래일. 60/120/250 지원
    top_k: int = 5
    min_gap_days: int = 250  # 거래일. 선택된 국면끼리, 그리고 as_of와의 최소 간격
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    horizons: tuple[int, ...] = (21, 63, 126, 252)
    macro_series: tuple[str, ...] = DEFAULT_MACRO_SERIES
    change_days: int = 126  # 매크로 "방향" 특징의 기간 (거래일)

    # --- 계약 8.3의 고정 상수를 설정으로 노출한 것 (기본값이 계약값) ---
    prefilter: int = 300  # 유클리드 사전 필터로 남길 후보 수 (이후 DTW)
    band_frac: float = 0.10  # DTW Sakoe-Chiba 밴드 폭 (창 길이 대비 비율)
    drawdown_weight: float = 0.2  # price_shape 안에서 낙폭 특징의 가중치
    zscore_min_periods: int = 60  # expanding z-score가 값을 내기 시작하는 최소 관측 수
    # 산업 사이클 월간 값은 실제로는 다음 달 초~중순에 발표된다. 0이면 계약대로
    # 날짜 그대로 ffill, 양수면 그 일수만큼 늦게 알게 된 것으로 취급한다(룩어헤드 완화).
    cycle_lag_days: int = 45

    # --- Phase 6 (PHASE6_CONTRACT 23·24절) ---
    mode: str = "shape"  # "shape"(모양 우선, 기존) | "regime"(환경 우선, regime.py)
    # 적합 기준(23절). 기준값은 계약에 고정되어 있다. 백테스트 결과를 보고 바꾸지 않는다.
    fit_drawdown_pp: float = 8.0  # 낙폭 차이 ≤ 8%p
    fit_dd_days_ratio: float = 0.40  # 고점 이후 거래일 수 차이 ≤ 40% (긴 쪽 기준)
    fit_rate_pp: float = 2.0  # 기준금리 수준 차이 ≤ 2.0%p (그리고 6개월 방향 같음)
    fit_vol_ratio: float = 0.40  # 60일 실현 변동성 차이 ≤ 40% (큰 쪽 기준)
    fit_rate_dir_pp: float = (
        0.25  # 최근 change_days 동안 DFF 변화 ±0.25%p → 인상/인하, 그 안은 동결
    )
    fit_vol_days: int = 60
    fit_min_applicable: int = 2  # 상위 K 중 ok 이상이 이 수 이상이면 applicable
    # 월간 발표 시리즈(CPI, M2)의 발표 지연(일). 날짜 그대로 쓰면 룩어헤드가 되므로
    # cycle_lag_days처럼 늦춰서 "그때 알 수 있었던 값"만 쓴다.
    monthly_lag_days: int = 45
    regime_weights: dict[str, float] = field(default_factory=lambda: dict(REGIME_WEIGHTS))
    regime_clip_sigma: float = 3.0  # 표준화 특징의 ±3σ 클리핑

    def __post_init__(self) -> None:
        if self.window < 10:
            raise ValueError("window는 10 이상이어야 한다")
        if self.top_k < 1:
            raise ValueError("top_k는 1 이상이어야 한다")
        if self.min_gap_days < 1:
            raise ValueError("min_gap_days는 1 이상이어야 한다")
        if not self.weights or any(w < 0 for w in self.weights.values()):
            raise ValueError("weights는 비어 있지 않고 모두 0 이상이어야 한다")
        if sum(self.weights.values()) <= 0:
            raise ValueError("weights 합은 0보다 커야 한다")
        if not 0.0 <= self.drawdown_weight <= 1.0:
            raise ValueError("drawdown_weight는 0~1")
        if not 0.0 <= self.band_frac <= 1.0:
            raise ValueError("band_frac는 0~1")
        if self.mode not in ("shape", "regime"):
            raise ValueError("mode는 'shape' 또는 'regime'")
        if not self.regime_weights or any(w < 0 for w in self.regime_weights.values()):
            raise ValueError("regime_weights는 비어 있지 않고 모두 0 이상이어야 한다")
        if sum(self.regime_weights.values()) <= 0:
            raise ValueError("regime_weights 합은 0보다 커야 한다")
