"""응답 스키마(pydantic). 모양은 docs/PHASE5_CONTRACT.md 17절 그대로.

날짜는 YYYY-MM-DD 문자열, 수익률은 소수, 점수는 0~1, 결측은 null.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Health(BaseModel):
    ok: bool
    last_trading_day: str | None
    layers_missing: list[str]
    error: str | None = None


class MatchOut(BaseModel):
    rank: int
    start: str
    end: str
    scores: dict[str, float | None]
    combined: float | None
    forward: dict[str, float | None]


class ForwardSummaryRow(BaseModel):
    horizon: int
    median: float | None
    p10: float | None
    p90: float | None
    n_up: int
    n_down: int
    n: int


class SimilarityOut(BaseModel):
    as_of: str
    window: int
    top_k: int
    n_candidates: int
    layers_missing: list[str]
    weights_used: dict[str, float]
    matches: list[MatchOut]
    forward_summary: list[ForwardSummaryRow]
    # --- Phase 6 (계약 23·24절). 엔진이 아직 만들지 않으면 전부 null ---
    mode: str | None = None  # "shape" | "regime"
    # matches와 같은 순서의 적합 판정 {conditions:[{name,then,now,..,passed}],n_passed,status}
    fit: list[dict[str, Any]] | None = None
    applicable: bool | None = None  # False면 "현재와 충분히 닮은 과거 사례가 없다"
    applicable_n: int | None = None
    forward_summary_source: str | None = None  # "matches" | "baseline"
    # 전체 과거(as_of 이하) 기준선 분포. 닮은 국면 분포와 나란히 보여 주려고 항상 채운다.
    forward_summary_baseline: list[ForwardSummaryRow] | None = None


class Point(BaseModel):
    d: int
    v: float


class EventMarker(BaseModel):
    d: int
    date: str
    kind: str
    title: str


class CurrentPath(BaseModel):
    start: str
    end: str
    points: list[Point]
    events: list[EventMarker]


class MatchPath(BaseModel):
    rank: int
    start: str
    end: str
    window_points: list[Point]
    after_points: list[Point]
    events: list[EventMarker]


class OverlayOut(BaseModel):
    current: CurrentPath
    matches: list[MatchPath]


class SignalOut(BaseModel):
    """체크리스트 신호 하나 (계약 PHASE6C 30절). on=None은 데이터 없음, 켜짐은 값 op 임계값."""

    id: str
    group: str  # bottom | bubble | heat | crisis
    label: str
    on: bool | None
    value: float | None
    threshold: float
    unit: str
    op: str


class ChecklistMatchOut(BaseModel):
    rank: int
    end: str
    signals: list[SignalOut]


class ChecklistOut(BaseModel):
    now: list[SignalOut]
    matches: list[ChecklistMatchOut]


class ThenNowOut(BaseModel):
    then: float | None
    now: float | None


class ThenNowIntOut(BaseModel):
    then: int | None
    now: int | None


class ExplainMatch(BaseModel):
    start: str
    end: str
    combined: float | None
    scores: dict[str, float | None]


class ExplainPrice(BaseModel):
    dtw_rank: int | None
    euclid_rank: int | None
    drawdown: ThenNowOut
    drawdown_days: ThenNowIntOut


class ExplainMacro(BaseModel):
    series: str
    label: str
    unit: str
    then: float | None = None
    now: float | None = None
    z_then: float | None = None
    z_now: float | None = None


class ExplainCycle(BaseModel):
    metric: str
    label: str
    then: float | None = None
    now: float | None = None


class ExplainOut(BaseModel):
    as_of: str
    match: ExplainMatch
    price: ExplainPrice
    macro: list[ExplainMacro]
    cycle: list[ExplainCycle]
    forward: dict[str, float | None]
    layers_missing: list[str] = Field(default_factory=list)
    comment: str = ""  # 평가표 코멘트 (계약 26절)


class UpcomingEvent(BaseModel):
    date: str
    kind: str
    title: str
    symbol: str | None
    scheduled: bool
    confirmed: bool


class EventImpactRow(BaseModel):
    id: int
    date: str
    kind: str
    title: str
    ret5: float | None
    ret_1m: float | None
    ret_3m: float | None
    source: str | None


class EventRef(BaseModel):
    id: int
    date: str
    kind: str
    title: str


class EventPathOut(BaseModel):
    event: EventRef
    points: list[Point]


class PositionItem(BaseModel):
    symbol: str
    quantity: float | None
    avg_price: float | None
    last_price: float | None
    market_value: float | None
    pnl: float | None
    pnl_rate: float | None
    currency: str | None


class PositionsLatest(BaseModel):
    snapshot_date: str
    items: list[PositionItem]


class ApplyRow(BaseModel):
    basis: str  # "sox" | "lev3"
    horizon: int
    median: float | None
    p10: float | None
    p90: float | None


class ApplyOut(BaseModel):
    position_value: float
    currency: str | None
    n: int
    rows: list[ApplyRow]


class ReportLatest(BaseModel):
    run_date: str
    markdown: str | None
