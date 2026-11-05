"""백테스트 preset: 사전 등록된 (모드, top_k, min_gap) 조합. 계약: docs/PLAN_PHASE6B.md 2절.

Phase 6B의 질문은 "닮은 과거를 10·20·30개로 뽑아 분포를 만들면 기준선보다 나은가"이다.
arm 9개와 min_gap은 문서에 고정되어 있고, 결과를 보고 바꾸지 않는다.

용어:
- arm: 비교 대상 하나 (예: shape_k10 = 모양 우선, 상위 10개, 창 간 최소 간격 250거래일).
- 실질 표본 수(n_eff): 분포를 만든 창 가운데 서로 EFFECTIVE_GAP(126거래일, 약 6개월) 이상 떨어진
  창의 수. min_gap이 작으면 이웃한 날짜(거의 같은 국면)가 여러 개 뽑혀 k보다 작아진다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EFFECTIVE_GAP = 126  # 거래일. "서로 다른 국면"으로 셀 최소 간격 (PLAN_PHASE6B 3절)
HIT_RANGE = (0.60, 0.95)  # 적중률이 "현실적인 구간 폭"으로 보는 범위 (PLAN_PHASE6B 3절)


@dataclass(frozen=True)
class Arm:
    name: str  # DB의 mode 컬럼에 들어가는 이름
    mode: str  # baseline | shape | regime (엔진 모드)
    top_k: int | None  # baseline은 None
    min_gap: int | None  # 거래일. baseline은 None
    reference: bool = False  # 참고 줄(Phase 6과 동일 설정) 여부

    @property
    def label(self) -> str:
        if self.mode == "baseline":
            return "기준선"
        base = {"shape": "모양 우선", "regime": "환경 우선"}[self.mode]
        return f"{base} k={self.top_k}"


# PLAN_PHASE6B 2절 표 그대로. 순서도 문서 순서.
PHASE6B_ARMS: tuple[Arm, ...] = (
    Arm("baseline", "baseline", None, None),
    Arm("shape_k5", "shape", 5, 250, reference=True),
    Arm("regime_k5", "regime", 5, 250, reference=True),
    Arm("shape_k10", "shape", 10, 250),
    Arm("regime_k10", "regime", 10, 250),
    Arm("shape_k20", "shape", 20, 126),
    Arm("regime_k20", "regime", 20, 126),
    Arm("shape_k30", "shape", 30, 63),
    Arm("regime_k30", "regime", 30, 63),
)

PRESETS: dict[str, tuple[Arm, ...]] = {"phase6b": PHASE6B_ARMS}


def get_preset(name: str) -> tuple[Arm, ...]:
    try:
        return PRESETS[name]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 preset: {name!r} (가능: {', '.join(PRESETS)})") from exc


def arm_by_name(arms: tuple[Arm, ...], name: str) -> Arm:
    for a in arms:
        if a.name == name:
            return a
    raise KeyError(name)


def effective_count(positions, gap: int = EFFECTIVE_GAP) -> int:
    """서로 `gap` 이상 떨어진 위치의 최대 개수 (1차원이므로 정렬 후 탐욕 선택이 최적).

    positions: 거래일 위치(정수)들. 비어 있으면 0. 예: [0, 50, 200, 330], gap=126 → 0, 200, 330
    → 3개 (50은 0과 50밖에 안 떨어져 제외).
    """
    pos = np.sort(np.asarray(list(positions), dtype="int64"))
    if len(pos) == 0:
        return 0
    count = 1
    last = int(pos[0])
    for p in pos[1:]:
        if int(p) - last >= gap:
            count += 1
            last = int(p)
    return count


__all__ = [
    "EFFECTIVE_GAP",
    "HIT_RANGE",
    "PHASE6B_ARMS",
    "PRESETS",
    "Arm",
    "arm_by_name",
    "effective_count",
    "get_preset",
]
