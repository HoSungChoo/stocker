"""유사도 엔진 설정. 계약: docs/DATA_CONTRACT.md 8.2."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {"price_shape": 0.5, "macro": 0.3, "semi_cycle": 0.2}
DEFAULT_MACRO_SERIES = ("DFF", "DGS10", "T10Y2Y", "DCOILWTICO", "DTWEXBGS", "VIXCLS")
CYCLE_METRICS = ("wsts_sales_world_usd_m", "mu_gross_margin")


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
