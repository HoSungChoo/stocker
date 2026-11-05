"""채점. 계약: PHASE6_CONTRACT 25절, 승리 조건은 PLAN_PHASE6 1절(실험 전 고정).

용어:
- 구간 점수(interval score): 10~90% 구간의 폭 + 실제값이 구간 밖이면 벗어난 거리 × (2/α).
  α=0.2(80% 구간). 낮을수록 좋다. "실제값을 품으면서도 좁은" 분포가 점수가 낮다. 주 지표.
- 적중률: 실제값이 10~90% 안에 든 비율. 80%에 가까울수록 좋다.
- 방향 일치: 중앙값의 부호와 실제 부호가 같은 비율.
- 해당 없음 비율: 적합 기준(23절)에서 "닮은 과거가 없다"(applicable=False)로 판정된 시점의 비율.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

ALPHA = 0.2
MODES = ("baseline", "shape", "shape_env", "regime")
# 승리 조건 판정 대상. shape_env는 참고 줄(가중치만 바꾼 모양 우선)이라 승자가 될 수 없다.
CONTENDERS = ("shape", "regime")
REFERENCE_MODES = ("shape_env",)
# 시대 구분 (PLAN_PHASE6 1절). as_of의 연도로 나눈다.
ERAS: dict[str, tuple[int, int]] = {
    "1999–2006": (1999, 2006),
    "2007–2012": (2007, 2012),
    "2013–2019": (2013, 2019),
    "2020–2025": (2020, 2025),
}
MIN_ERAS_TO_WIN = 3


def interval_score(p10: float, p90: float, realized: float, alpha: float = ALPHA) -> float:
    """(p90 - p10) + (2/α)·(p10 - y)·[y < p10] + (2/α)·(y - p90)·[y > p90]. 입력에 NaN이면 NaN."""
    if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in (p10, p90, realized)):
        return float("nan")
    width = p90 - p10
    penalty = 0.0
    if realized < p10:
        penalty += (2.0 / alpha) * (p10 - realized)
    if realized > p90:
        penalty += (2.0 / alpha) * (realized - p90)
    return float(width + penalty)


def interval_score_series(df: pd.DataFrame, alpha: float = ALPHA) -> pd.Series:
    """df(p10, p90, realized) → 행별 구간 점수(벡터 버전; interval_score와 같은 값)."""
    p10 = df["p10"].to_numpy(dtype="float64")
    p90 = df["p90"].to_numpy(dtype="float64")
    y = df["realized"].to_numpy(dtype="float64")
    width = p90 - p10
    below = np.where(y < p10, p10 - y, 0.0)
    above = np.where(y > p90, y - p90, 0.0)
    out = width + (2.0 / alpha) * (below + above)
    out = np.where(np.isnan(p10) | np.isnan(p90) | np.isnan(y), np.nan, out)
    return pd.Series(out, index=df.index)


def hit_series(df: pd.DataFrame) -> pd.Series:
    """실제값이 [p10, p90] 안이면 1, 밖이면 0, NaN이면 NaN."""
    ok = (df["realized"] >= df["p10"]) & (df["realized"] <= df["p90"])
    return ok.astype("float64").where(df[["p10", "p90", "realized"]].notna().all(axis=1))


def direction_series(df: pd.DataFrame) -> pd.Series:
    """sign(median) == sign(realized)면 1, 아니면 0, NaN이면 NaN."""
    same = np.sign(df["median"]) == np.sign(df["realized"])
    return same.astype("float64").where(df[["median", "realized"]].notna().all(axis=1))


def era_of(ts) -> str | None:
    y = pd.Timestamp(ts).year
    for name, (lo, hi) in ERAS.items():
        if lo <= y <= hi:
            return name
    return None


@dataclass
class Summary:
    mode: str
    era: str  # "전체" 또는 ERAS의 키
    n: int  # 채점된 시점 수 (realized가 있는 것)
    interval_score: float
    hit_rate: float
    direction_rate: float
    not_applicable_rate: float  # applicable=False 비율 (baseline은 NaN)
    baseline_rate: float  # ok 이상이 0개여서 기준선 분포로 대체된 비율 (baseline은 NaN)
    mean_n: float  # 분포를 만든 국면 수의 평균


def _rate(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.mean()) if len(s) else float("nan")


def summarize(df: pd.DataFrame, mode: str, era: str, top_k: int = 5) -> Summary:
    """한 (mode, era) 묶음의 지표. top_k: 기준선 대체 여부를 n으로 가려내는 기준."""
    d = df.copy()
    scored = d.dropna(subset=["p10", "p90", "realized"])
    na_rate = float("nan")
    base_rate = float("nan")
    if mode != "baseline" and len(d):
        app = d["applicable"]
        if app.notna().any():
            not_app = app == False  # noqa: E712
            na_rate = float(not_app.sum() / app.notna().sum())
            # 기준선 대체: applicable=False이고 국면 수 n이 top_k보다 큼(전체 과거 창의 수)
            base_rate = float((not_app & (d["n"] > top_k)).sum() / app.notna().sum())
    return Summary(
        mode=mode,
        era=era,
        n=int(len(scored)),
        interval_score=_rate(interval_score_series(scored)) if len(scored) else float("nan"),
        hit_rate=_rate(hit_series(scored)) if len(scored) else float("nan"),
        direction_rate=_rate(direction_series(scored)) if len(scored) else float("nan"),
        not_applicable_rate=na_rate,
        baseline_rate=base_rate,
        mean_n=_rate(d["n"].astype("float64")) if "n" in d and len(d) else float("nan"),
    )


def aggregate(runs: pd.DataFrame, horizon: int, top_k: int = 5) -> pd.DataFrame:
    """backtest_runs 형식 → (mode, era) 지표 표. era="전체"가 먼저, 그다음 ERAS 순.

    columns: mode, era, n, interval_score, hit_rate, direction_rate, not_applicable_rate,
             baseline_rate, mean_n
    """
    d = runs[runs["horizon"] == horizon].copy()
    d["era"] = d["as_of"].map(era_of)
    rows: list[Summary] = []
    modes = [m for m in MODES if m in set(d["mode"])] + sorted(set(d["mode"]) - set(MODES))
    for mode in modes:
        dm = d[d["mode"] == mode]
        rows.append(summarize(dm, mode, "전체", top_k))
        for era in ERAS:
            rows.append(summarize(dm[dm["era"] == era], mode, era, top_k))
    return pd.DataFrame([r.__dict__ for r in rows])


@dataclass
class Verdict:
    """PLAN_PHASE6 1절 승리 조건의 기계적 판정."""

    beats_baseline: dict[str, bool]  # mode → 전체 IS < 기준선 AND 시대 4개 중 3개 이상 <
    eras_won: dict[str, list[str]]  # mode → 기준선을 이긴 시대들
    overall_is: dict[str, float]  # mode → 전체 구간 점수
    winner: str | None  # "shape" | "regime" | None(둘 다 기준선을 못 이김)
    conclusion: str  # 한 줄 결론 (기계 생성)


def verdict(agg: pd.DataFrame) -> Verdict:
    """aggregate() 결과 → 승리 조건 판정. 기준선 행이 없으면 ValueError."""
    if not (agg["mode"] == "baseline").any():
        raise ValueError("기준선(baseline) 결과가 없어 판정할 수 없다")

    def is_of(mode: str, era: str) -> float:
        row = agg[(agg["mode"] == mode) & (agg["era"] == era)]
        return float(row["interval_score"].iloc[0]) if len(row) else float("nan")

    base_all = is_of("baseline", "전체")
    beats: dict[str, bool] = {}
    eras_won: dict[str, list[str]] = {}
    overall: dict[str, float] = {"baseline": base_all}
    for mode in (*CONTENDERS, *REFERENCE_MODES):
        if not (agg["mode"] == mode).any():
            continue
        all_is = is_of(mode, "전체")
        overall[mode] = all_is
        won = [
            era
            for era in ERAS
            if not math.isnan(is_of(mode, era))
            and not math.isnan(is_of("baseline", era))
            and is_of(mode, era) < is_of("baseline", era)
        ]
        eras_won[mode] = won
        beats[mode] = bool(
            not math.isnan(all_is) and all_is < base_all and len(won) >= MIN_ERAS_TO_WIN
        )

    winners = [m for m, b in beats.items() if b and m in CONTENDERS]
    if not winners:
        winner = None
        conclusion = (
            "둘 다 기준선을 이기지 못했다. 결론: 매칭(모양 우선·환경 우선 모두)이 "
            "전체 과거 분포보다 12개월 결과의 분포를 좁히지 못한다."
        )
    elif len(winners) == 1:
        winner = winners[0]
        label = {"shape": "모양 우선", "regime": "환경 우선"}[winner]
        conclusion = (
            f"{label}({winner})이 승리 조건을 만족한다: 전체 구간 점수가 기준선보다 낮고 "
            f"시대 {len(eras_won[winner])}/4개에서도 낮다."
        )
    else:
        # 둘 다 조건 충족 → 전체 구간 점수가 더 낮은 쪽 (사전 등록 조건 밖의 동점 처리, 문서에 명시)
        winner = min(winners, key=lambda m: overall[m])
        label = {"shape": "모양 우선", "regime": "환경 우선"}[winner]
        conclusion = (
            f"두 모드 모두 승리 조건을 만족했고, 전체 구간 점수가 더 낮은 {label}({winner})을 "
            "승자로 적는다(동점 처리 규칙: 전체 구간 점수)."
        )
    return Verdict(
        beats_baseline=beats,
        eras_won=eras_won,
        overall_is=overall,
        winner=winner,
        conclusion=conclusion,
    )


__all__ = [
    "ALPHA",
    "ERAS",
    "MODES",
    "Summary",
    "Verdict",
    "aggregate",
    "direction_series",
    "era_of",
    "hit_series",
    "interval_score",
    "interval_score_series",
    "summarize",
    "verdict",
]
