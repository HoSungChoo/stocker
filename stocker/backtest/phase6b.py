"""Phase 6B 보고서 (`docs/BACKTEST_RESULTS_K.md`). 계약: docs/PLAN_PHASE6B.md 3·4절.

숫자는 엔진이, 판정은 3절 승리 조건으로, 결론은 4절 해석 규칙을 **기계적으로** 적용해 만든다.
사람이 결과를 보고 고쳐 쓰지 않는다.

해석 규칙(4절) 요약:
- k=10·20에서 기준선을 이기는지가 핵심. k=30에서만 이기면 "매칭 효과는 미약".
- 어느 k에서도 못 이기면 "매칭은 12개월 분포를 좁히지 못한다" 확정
  → 상위 국면은 통계가 아니라 사례로.
- 환경 우선만 이기면 "환경 매칭은 효과가 있다, 모양 매칭은 없다".
k=5 두 arm은 Phase 6과 같은 설정의 참고 줄이라 규칙 적용 대상이 아니다(표에는 넣는다).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stocker.backtest.metrics import (
    ALPHA,
    ERAS,
    MIN_ERAS_TO_WIN,
    BaselineComparison,
    aggregate,
    compare_to_baseline,
)
from stocker.backtest.presets import EFFECTIVE_GAP, HIT_RANGE, Arm, get_preset

DEFAULT_PATH_K = Path("docs/BACKTEST_RESULTS_K.md")
MODE_KO = {"shape": "모양 우선", "regime": "환경 우선", "baseline": "기준선"}


def _f(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.0%}"


def _label(arm: Arm) -> str:
    return f"{arm.label}({arm.name})"


def _in_range(hit: float) -> bool | None:
    if hit is None or (isinstance(hit, float) and math.isnan(hit)):
        return None
    return HIT_RANGE[0] <= hit <= HIT_RANGE[1]


@dataclass
class ArmResult:
    arm: Arm
    comparison: BaselineComparison | None  # 기준선 arm은 None
    hit_rate: float
    hit_in_range: bool | None  # 적중률이 HIT_RANGE 안인가 (NaN이면 None)
    mean_n_eff: float
    not_applicable_rate: float
    baseline_rate: float

    @property
    def wins(self) -> bool:
        return bool(self.comparison and self.comparison.beats_baseline)


@dataclass
class Interpretation:
    rows: list[ArmResult]
    winners: list[str]  # 승리 조건을 만족한 비참고 arm 이름들
    core_winners: list[str]  # 그중 k=10·20
    winning_modes: list[str]  # 이긴 비참고 arm의 엔진 모드 집합(정렬)
    verdict: str  # 승리 조건 판정 한 줄
    conclusion: str  # 4절 해석 규칙을 적용한 결론 (기계 생성)


def arm_results(agg: pd.DataFrame, arms: tuple[Arm, ...]) -> list[ArmResult]:
    comp = compare_to_baseline(agg, [a.name for a in arms])
    rows: list[ArmResult] = []
    for arm in arms:
        r = agg[(agg["mode"] == arm.name) & (agg["era"] == "전체")]
        if r.empty:
            continue
        r = r.iloc[0]
        hit = float(r["hit_rate"])
        rows.append(
            ArmResult(
                arm=arm,
                comparison=comp.get(arm.name),
                hit_rate=hit,
                hit_in_range=_in_range(hit),
                mean_n_eff=float(r["mean_n_eff"]),
                not_applicable_rate=float(r["not_applicable_rate"]),
                baseline_rate=float(r["baseline_rate"]),
            )
        )
    return rows


def interpret(agg: pd.DataFrame, arms: tuple[Arm, ...]) -> Interpretation:
    """3절 승리 조건 → 4절 해석 규칙을 순서대로 적용한다."""
    rows = arm_results(agg, arms)
    contenders = [r for r in rows if r.arm.mode != "baseline" and not r.arm.reference]
    winners = [r.arm.name for r in contenders if r.wins]
    core = [r.arm.name for r in contenders if r.wins and r.arm.top_k in (10, 20)]
    modes = sorted({r.arm.mode for r in contenders if r.wins})

    if not winners:
        verdict = "어느 k(10·20·30)에서도, 모양 우선·환경 우선 모두 승리 조건을 만족하지 못했다."
        conclusion = (
            "매칭은 12개월 분포를 좁히지 못한다(확정). 닮은 과거를 5개에서 30개까지 넉넉히 뽑아도 "
            "전체 과거 분포(기준선)보다 나은 분포를 만들지 못했다. 서비스의 상위 국면은 "
            "통계가 아니라 사례로 다룬다(화면 문구 유지)."
        )
    else:
        verdict = f"승리 조건을 만족한 arm: {', '.join(winners)}."
        if core:
            head = f"k=10·20에서 기준선을 이겼다({', '.join(core)}). 매칭 효과가 있다."
        else:
            head = (
                f"k=30에서만 기준선을 이겼다({', '.join(winners)}). 해석 규칙에 따라 "
                '"매칭 효과는 미약"으로 적는다(k가 커질수록 기준선에 가까워지는 것은 당연하다).'
            )
        if modes == ["regime"]:
            tail = " 환경 매칭은 효과가 있다, 모양 매칭은 없다."
        elif modes == ["shape"]:
            tail = (
                " 모양 매칭만 이겼고 환경 매칭은 이기지 못했다"
                "(사전 등록 규칙의 대칭 해석: 모양 매칭은 효과가 있다, 환경 매칭은 없다)."
            )
        else:
            tail = " 모양 우선·환경 우선 모두 이긴 k가 있다."
        conclusion = head + tail
    return Interpretation(
        rows=rows,
        winners=winners,
        core_winners=core,
        winning_modes=modes,
        verdict=verdict,
        conclusion=conclusion,
    )


# --------------------------------------------------------------------------- 표
def _verdict_table(interp: Interpretation) -> str:
    lines = [
        "| arm | 모드 | top_k | min_gap | 전체 구간 점수 ↓ | 기준선을 이긴 시대 | 승리 조건 "
        f"| 적중률 ({HIT_RANGE[0]:.0%}~{HIT_RANGE[1]:.0%} 안?) | 평균 실질 표본 수 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in interp.rows:
        a = r.arm
        if a.mode == "baseline":
            lines.append(
                f"| {a.name} | 기준선 | — | — | {_f(_baseline_is(interp))} | — | — | "
                f"{_pct(r.hit_rate)} ({_yn(r.hit_in_range)}) | — |"
            )
            continue
        c = r.comparison
        status = "충족" if r.wins else "미충족"
        if a.reference:
            status += " (참고 줄: Phase 6과 동일)"
        lines.append(
            f"| {a.name} | {MODE_KO[a.mode]} | {a.top_k} | {a.min_gap} | {_f(c.overall_is)} | "
            f"{len(c.eras_won)}/4 ({', '.join(c.eras_won) or '없음'}) | {status} | "
            f"{_pct(r.hit_rate)} ({_yn(r.hit_in_range)}) | {_f(r.mean_n_eff, 1)} |"
        )
    return "\n".join(lines)


def _baseline_is(interp: Interpretation) -> float:
    for r in interp.rows:
        if r.comparison is not None:
            return r.comparison.baseline_is
    return float("nan")


def _yn(x: bool | None) -> str:
    return "n/a" if x is None else ("안" if x else "밖")


def _table(agg: pd.DataFrame, era: str, arms: tuple[Arm, ...]) -> str:
    rows = agg[agg["era"] == era].set_index("mode")
    lines = [
        "| arm | 시점 수 | 구간 점수 ↓ | 적중률 | 방향 일치 | 해당 없음 비율 | 기준선 대체 비율 "
        "| 평균 국면 수 | 평균 실질 표본 수 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in arms:
        if arm.name not in rows.index:
            continue
        r = rows.loc[arm.name]
        lines.append(
            f"| {_label(arm)} | {int(r['n'])} | {_f(r['interval_score'])} | "
            f"{_pct(r['hit_rate'])} | {_pct(r['direction_rate'])} | "
            f"{_pct(r['not_applicable_rate'])} | {_pct(r['baseline_rate'])} | "
            f"{_f(r['mean_n'], 1)} | {_f(r['mean_n_eff'], 1)} |"
        )
    return "\n".join(lines)


def _era_matrix(agg: pd.DataFrame, arms: tuple[Arm, ...]) -> str:
    """시대 × arm의 구간 점수 (✓ = 기준선보다 낮음). 열은 arm 이름(짧게)."""
    present = [a for a in arms if (agg["mode"] == a.name).any()]
    lines = [
        "| 시대 | " + " | ".join(a.name for a in present) + " |",
        "|---|" + "---|" * len(present),
    ]
    for era in ["전체", *ERAS]:
        base = agg[(agg["mode"] == "baseline") & (agg["era"] == era)]
        base_is = float(base["interval_score"].iloc[0]) if len(base) else float("nan")
        cells = []
        for a in present:
            row = agg[(agg["mode"] == a.name) & (agg["era"] == era)]
            v = float(row["interval_score"].iloc[0]) if len(row) else float("nan")
            mark = ""
            if a.mode != "baseline" and not math.isnan(v) and not math.isnan(base_is):
                mark = " ✓" if v < base_is else " ✗"
            cells.append(_f(v) + mark)
        lines.append(f"| {era} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 쉬운 설명
def _first_k_in_range(interp: Interpretation, mode: str) -> int | None:
    ks = sorted(
        r.arm.top_k
        for r in interp.rows
        if r.arm.mode == mode and r.arm.top_k is not None and r.hit_in_range
    )
    return ks[0] if ks else None


def _easy_explanation(interp: Interpretation, horizon: int) -> str:
    months = round(horizon / 21)
    base_is = _baseline_is(interp)
    base_row = next((r for r in interp.rows if r.arm.mode == "baseline"), None)
    parts: list[str] = []
    parts.append(
        f"Phase 6에서는 닮은 과거를 5개만 뽑아 {months}개월 뒤 수익률의 10~90% 구간을 만들었는데, "
        "표본 5개의 10~90% 구간은 사실상 5개의 최소~최대라 너무 좁았다. 그래서 이번에는 같은 채점 "
        "방식으로 닮은 과거를 10·20·30개 뽑아 봤다. 구간 점수는 구간이 좁을수록, 실제값이 "
        "구간 안에 들수록 낮아진다(낮을수록 좋다). 기준선은 매칭 없이 전체 과거를 그대로 쓴 "
        '분포다. "실질 표본 수"는 뽑힌 창 가운데 서로 6개월(126거래일) 이상 떨어진 것의 수로, '
        "이웃한 날짜가 여러 개 뽑혀 사실상 같은 국면을 되풀이한 경우를 걸러 낸 진짜 표본 수다."
    )
    # k별 결과 (모드별로 한 문장)
    for mode in ("shape", "regime"):
        rows = [r for r in interp.rows if r.arm.mode == mode and not r.arm.reference]
        if not rows:
            continue
        ref = next((r for r in interp.rows if r.arm.mode == mode and r.arm.reference), None)
        seg = []
        for r in rows:
            c = r.comparison
            seg.append(
                f"k={r.arm.top_k}(min_gap {r.arm.min_gap}) 구간 점수 {_f(c.overall_is)}, "
                f"시대 {len(c.eras_won)}/4 승, 적중률 {_pct(r.hit_rate)}, "
                f"실질 표본 수 {_f(r.mean_n_eff, 1)}"
            )
        ref_txt = (
            f" 참고로 k=5(Phase 6과 동일)는 {_f(ref.comparison.overall_is)}였다." if ref else ""
        )
        parts.append(
            f"{MODE_KO[mode]}: 기준선 {_f(base_is)}에 대해 " + "; ".join(seg) + "." + ref_txt
        )
    # 적중률이 현실 범위에 들어오는 k
    lo, hi = HIT_RANGE
    rng = []
    for mode in ("shape", "regime"):
        k = _first_k_in_range(interp, mode)
        rng.append(
            f"{MODE_KO[mode]}은 k={k}부터"
            if k is not None
            else f"{MODE_KO[mode]}은 어느 k에서도 못"
        )
    base_hit = _pct(base_row.hit_rate) if base_row else "n/a"
    parts.append(
        f"적중률이 현실 범위({lo:.0%}~{hi:.0%})에 들어오는 k: {', '.join(rng)} 들어온다. "
        f"기준선의 적중률은 {base_hit}이다. 적중률이 범위 아래면 구간이 너무 좁아 실제값을 "
        "자주 놓친 것이고, 위면 구간이 너무 넓어 정보가 없는 것이다."
    )
    # 실질 표본 수
    eff = [
        f"{MODE_KO[r.arm.mode]} k={r.arm.top_k}: {_f(r.mean_n_eff, 1)}"
        for r in interp.rows
        if r.arm.mode != "baseline" and not r.arm.reference
    ]
    parts.append(
        f"실질 표본 수(서로 {EFFECTIVE_GAP}거래일 이상 떨어진 창)의 평균은 "
        + ", ".join(eff)
        + "이다. min_gap을 126·63으로 줄인 k=20·30은 이웃 날짜가 여러 개 뽑혀 k보다 작다. "
        '즉 "닮은 과거 30개"가 실제로는 그보다 적은 국면을 되풀이한 것이다(PLAN_PHASE6B 6절).'
    )
    # 환경 vs 모양
    diff = []
    for k in (10, 20, 30):
        s = next((r for r in interp.rows if r.arm.mode == "shape" and r.arm.top_k == k), None)
        g = next((r for r in interp.rows if r.arm.mode == "regime" and r.arm.top_k == k), None)
        if s and g:
            gi, si = g.comparison.overall_is, s.comparison.overall_is
            better = "환경 우선" if gi < si else "모양 우선"
            diff.append(f"k={k}에서는 {better}이 {_f(abs(gi - si))} 낮다")
    if diff:
        parts.append("환경 우선과 모양 우선의 차이: " + "; ".join(diff) + ".")
    parts.append("판정(3절 승리 조건): " + interp.verdict)
    parts.append("결론(4절 해석 규칙, 기계 적용): " + interp.conclusion)
    parts.append(
        "한계: 322개 시점은 서로 겹치고 실질 독립 사이클은 6~8개다. k=20·30은 이웃 날짜 중복으로 "
        '"닮은 과거 n개"의 의미가 약하다(실질 표본 수를 같이 본다). 설정·승리 조건·해석 규칙은 '
        "실험 전에 `docs/PLAN_PHASE6B.md`에 고정했고 결과를 보고 바꾸지 않았다."
    )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- 문서
def build_report_k(
    runs: pd.DataFrame,
    arms: tuple[Arm, ...] | None = None,
    horizon: int = 252,
    generated_at: str | None = None,
    preset: str = "phase6b",
) -> str:
    """backtest_runs 형식(mode=arm 이름) → `docs/BACKTEST_RESULTS_K.md` 전체."""
    arms = arms or get_preset(preset)
    names = [a.name for a in arms]
    top_k = {a.name: a.top_k for a in arms if a.top_k is not None}
    agg = aggregate(runs, horizon, top_k=top_k, modes=names)
    interp = interpret(agg, arms)
    d = runs[runs["horizon"] == horizon]
    n_dates = int(d["as_of"].nunique())
    span = f"{d['as_of'].min().date()} ~ {d['as_of'].max().date()}" if n_dates else "(시점 없음)"
    stamp = generated_at or pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    has_eff = "n_eff" in runs and runs["n_eff"].notna().any()
    lines = [
        "# 백테스트 결과 (Phase 6B): 닮은 과거를 10·20·30개 뽑으면 기준선을 이기는가",
        "",
        f"생성 {stamp} (`python -m stocker.backtest report --preset {preset}`). 이 문서는 자동 "
        "생성된다. arm·min_gap·승리 조건·해석 규칙은 `docs/PLAN_PHASE6B.md`에 실험 전 고정했고, "
        "결과를 보고 바꾸지 않았다. Phase 6 결과(`docs/BACKTEST_RESULTS.md`)는 수정하지 않는다.",
        "",
        f"- 평가 시점: 매월 말 거래일 {n_dates}개 ({span}). 각 시점은 그날까지의 데이터만 쓴다.",
        f"- 채점 horizon: {horizon}거래일(약 {round(horizon / 21)}개월). "
        f"구간 점수 α={ALPHA} (구간 밖 거리 × {2 / ALPHA:.0f}).",
        f"- 승리 조건: 전체 구간 점수 < 기준선 **그리고** 시대 4개 중 {MIN_ERAS_TO_WIN}개 이상 "
        "< 기준선.",
        "- 적합 기준(PHASE6_CONTRACT 23절)은 그대로: ok 이상만으로 분포, ok 이상 2개 이상이면 "
        "applicable, 0개면 기준선 분포로 채점.",
        f"- 실질 표본 수: 분포를 만든 창 중 서로 {EFFECTIVE_GAP}거래일 이상 떨어진 창의 수"
        + ("." if has_eff else " (이 실행 결과에는 없음 → n/a)."),
        "",
        "## 판정",
        "",
        f"**{interp.conclusion}**",
        "",
        _verdict_table(interp),
        "",
        "## 쉬운 설명",
        "",
        _easy_explanation(interp, horizon),
        "",
        "## 전체 기간",
        "",
        _table(agg, "전체", arms),
        "",
        "## 시대별 구간 점수 (✓ = 기준선보다 낮음)",
        "",
        _era_matrix(agg, arms),
        "",
    ]
    for era in ERAS:
        lines += [f"## 시대 {era}", "", _table(agg, era, arms), ""]
    lines += [
        "## 지표 설명",
        "",
        "- **구간 점수**: (p90 − p10) + (2/α)×(실제값이 구간 밖으로 벗어난 거리). "
        "낮을수록 좋다. 주 지표.",
        f"- **적중률**: 실제값이 10~90% 구간 안에 든 비율. {HIT_RANGE[0]:.0%}~{HIT_RANGE[1]:.0%}면 "
        "구간 폭이 현실적이라고 본다(아래면 너무 좁고, 위면 너무 넓다).",
        "- **방향 일치**: 중앙값의 부호와 실제값의 부호가 같은 비율.",
        "- **해당 없음 비율**: 적합 기준에서 ok 이상인 국면이 2개 미만(applicable=False)인 "
        "시점의 비율.",
        "- **기준선 대체 비율**: 그중 ok 이상이 0개여서 기준선 분포로 채점된 시점의 비율.",
        "- **평균 국면 수**: 분포를 만든 국면 수(n)의 평균. 기준선은 전체 과거 창의 수.",
        f"- **평균 실질 표본 수**: 분포를 만든 창 중 서로 {EFFECTIVE_GAP}거래일 이상 떨어진 "
        "창 수의 평균. 기준선 대체 시점은 제외.",
        "",
    ]
    return "\n".join(lines)


def write_report_k(
    runs: pd.DataFrame,
    path: Path | str = DEFAULT_PATH_K,
    horizon: int = 252,
    preset: str = "phase6b",
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_report_k(runs, None, horizon, None, preset), encoding="utf-8")
    return p


__all__ = [
    "DEFAULT_PATH_K",
    "ArmResult",
    "Interpretation",
    "arm_results",
    "build_report_k",
    "interpret",
    "write_report_k",
]
