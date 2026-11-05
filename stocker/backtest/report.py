"""docs/BACKTEST_RESULTS.md 생성. 숫자는 엔진이, 결론은 PLAN_PHASE6 1절 승리 조건으로 기계적으로.

"쉬운 설명" 단락도 숫자에서 규칙으로 만든다(사람이 결과를 보고 고쳐 쓰지 않는다).
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from stocker.backtest.metrics import (
    ALPHA,
    CONTENDERS,
    ERAS,
    MIN_ERAS_TO_WIN,
    REFERENCE_MODES,
    Verdict,
    aggregate,
    verdict,
)

MODE_LABEL = {
    "baseline": "기준선",
    "shape": "모양 우선",
    "shape_env": "모양 우선·환경 가중(참고)",
    "regime": "환경 우선",
}
DEFAULT_PATH = Path("docs/BACKTEST_RESULTS.md")


def _f(x: float, digits: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def _pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.0%}"


def _label(mode: str) -> str:
    return f"{MODE_LABEL.get(mode, mode)}({mode})"


def _table(agg: pd.DataFrame, era: str) -> str:
    rows = agg[agg["era"] == era]
    lines = [
        "| 모드 | 시점 수 | 구간 점수 ↓ | 적중률 (목표 80%) | 방향 일치 "
        "| 해당 없음 비율 | 기준선 대체 비율 | 평균 국면 수 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, r in rows.iterrows():
        lines.append(
            f"| {_label(r['mode'])} | {int(r['n'])} | {_f(r['interval_score'])} | "
            f"{_pct(r['hit_rate'])} | {_pct(r['direction_rate'])} | "
            f"{_pct(r['not_applicable_rate'])} | {_pct(r['baseline_rate'])} | "
            f"{_f(r['mean_n'], 1)} |"
        )
    return "\n".join(lines)


def _era_matrix(agg: pd.DataFrame) -> str:
    """시대 × 모드의 구간 점수 한 표 (기준선 대비 승/패 표시)."""
    modes = [m for m in ("baseline", *CONTENDERS, *REFERENCE_MODES) if (agg["mode"] == m).any()]
    lines = [
        "| 시대 | " + " | ".join(_label(m) for m in modes) + " |",
        "|---|" + "---|" * len(modes),
    ]
    for era in ["전체", *ERAS]:
        cells = []
        base = agg[(agg["mode"] == "baseline") & (agg["era"] == era)]
        base_is = float(base["interval_score"].iloc[0]) if len(base) else float("nan")
        for m in modes:
            row = agg[(agg["mode"] == m) & (agg["era"] == era)]
            v = float(row["interval_score"].iloc[0]) if len(row) else float("nan")
            mark = ""
            if m != "baseline" and not math.isnan(v) and not math.isnan(base_is):
                mark = " ✓" if v < base_is else " ✗"
            cells.append(_f(v) + mark)
        lines.append(f"| {era} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _easy_explanation(agg: pd.DataFrame, v: Verdict, horizon: int) -> str:
    """숫자에서 규칙으로 만든 쉬운 설명 단락."""
    base = v.overall_is.get("baseline", float("nan"))
    parts: list[str] = []
    months = round(horizon / 21)
    parts.append(
        f'이 실험은 매월 말마다 "그 시점까지의 데이터만으로" 각 방식이 낸 {months}개월 뒤 '
        "수익률 분포(10~90% 구간)를 실제 결과와 비교한 것이다. 구간 점수는 구간이 좁을수록, "
        "그리고 실제값이 구간 안에 들수록 낮아진다. 기준선은 매칭 없이 전체 과거를 그대로 쓴 "
        '분포라서, 매칭이 이것보다 점수가 낮지 않으면 "닮은 과거를 고르는 일"이 분포를 좁히는 데 '
        "도움이 안 됐다는 뜻이다."
    )
    # 무엇이 이겼나
    for m in (*CONTENDERS, *REFERENCE_MODES):
        if m not in v.overall_is:
            continue
        mine = v.overall_is[m]
        won = v.eras_won.get(m, [])
        with_data = [
            e for e in ERAS if (agg[(agg["mode"] == m) & (agg["era"] == e)]["n"] > 0).any()
        ]
        lost = [e for e in with_data if e not in won]
        rel = "낮아(좋아)" if mine < base else "높아(나빠)"
        parts.append(
            f"{_label(m)}은 전체 구간 점수가 {_f(mine)}로 기준선 {_f(base)}보다 {rel}졌고, "
            f"시대 4개 중 {len(won)}개({', '.join(won) or '없음'})에서 기준선을 이겼다"
            + (f"; 진 시대는 {', '.join(lost)}." if lost else ".")
            + (
                " 승리 조건(전체 + 시대 3개 이상)을 만족한다."
                if v.beats_baseline.get(m)
                else " 승리 조건(전체 + 시대 3개 이상)을 만족하지 못한다."
            )
            + (" (참고 줄: 승자 판정 대상이 아니다.)" if m in REFERENCE_MODES else "")
        )
    if all(m in v.overall_is for m in ("shape", "shape_env", "regime")):
        parts.append(_weights_vs_features(v))
    # 시대별로 갈렸나
    split = [m for m in CONTENDERS if m in v.eras_won and 1 <= len(v.eras_won[m]) <= 2]
    if split:
        parts.append(
            "시대별 결과가 갈린 모드("
            + ", ".join(_label(m) for m in split)
            + ")가 있다. 320개 시점은 서로 겹치는 표본이고 실질적인 독립 표본은 사이클 수인 "
            '6~8개에 가깝기 때문에, 시대별로 갈리면 "결론 없음"이 정직한 답이다.'
        )
    # 해당 없음
    na_rows = agg[(agg["era"] == "전체") & (agg["mode"] != "baseline")]
    na_txt = ", ".join(
        f"{_label(r['mode'])} {_pct(r['not_applicable_rate'])}"
        f"(그중 기준선 대체 {_pct(r['baseline_rate'])})"
        for _, r in na_rows.iterrows()
    )
    if na_txt:
        parts.append(
            '적합 기준("현재와 충분히 닮은 과거가 없으면 적용하지 않는다")에서 '
            f'"닮은 과거가 부족하다"(ok 이상 2개 미만)로 판정된 시점의 비율은 {na_txt}이다. '
            "ok 이상이 하나도 없는 시점은 기준선 분포로 채점되어 기준선과 같은 점수를 받는다. "
            "ok가 1개뿐인 시점은 그 1개로 분포를 만들어 구간이 한 점으로 좁아지므로, 실제값이 "
            "조금만 벗어나도 큰 벌점을 받는다(계약 23절 규칙 그대로). 그래서 매칭의 유불리는 "
            "ok 이상이 2개 이상인 시점에서 결정된다."
        )
    parts.append("결론(기계 판정): " + v.conclusion)
    parts.append(
        '한계: 환경 우선이 이기더라도 "외적 요소가 그래프를 결정한다"의 증명은 아니고, '
        '"환경이 닮은 시점들의 결과가 더 비슷하게 모였다"까지다. 내러티브는 VIX와 200일선 '
        "괴리라는 대리 지표만 썼다. 가중치·임계값은 실험 전에 고정했고 이 결과를 보고 "
        "바꾸지 않았다."
    )
    return "\n\n".join(parts)


def _weights_vs_features(v: Verdict) -> str:
    """shape_env(가중치만 바꾼 모양 우선)와 regime(잣대를 바꾼 것)의 차이를 읽는 문장."""
    s, se, r = v.overall_is["shape"], v.overall_is["shape_env"], v.overall_is["regime"]
    parts = [
        '참고 줄 "모양 우선·환경 가중"은 모양 우선 엔진 그대로에 가중치만 가격 0.1 / 매크로 0.6 / '
        f"업황 0.3으로 바꾼 것이다(구간 점수 {_f(se)}; 모양 우선 {_f(s)}, 환경 우선 {_f(r)})."
    ]
    gain_w = s - se  # 가중치만 바꿔서 얻은 개선
    gain_f = se - r  # 거기서 잣대(특징)까지 바꿔서 더 얻은 개선
    if gain_w > 0 and gain_f > 0:
        parts.append(
            f"가중치를 환경 쪽으로 옮긴 것만으로 {_f(gain_w)}가 좋아졌고, 잣대(특징)를 바꾼 것이 "
            f"{_f(gain_f)}를 더 좋게 했다. 환경 우선의 이점은 두 요인에 나뉜다."
        )
    elif gain_w > 0 >= gain_f:
        parts.append(
            f"가중치를 환경 쪽으로 옮긴 것만으로 {_f(gain_w)}가 좋아졌지만, 잣대(특징)까지 바꾼 "
            f"환경 우선은 그보다 {_f(-gain_f)} 나빴다. 차이는 주로 가중치 비율에서 온다."
        )
    elif gain_w <= 0 < gain_f:
        parts.append(
            f"가중치만 바꾼 것은 도움이 안 됐고({_f(-gain_w)} 나빠짐), 잣대(특징)를 바꾼 것이 "
            f"{_f(gain_f)}를 좋게 했다. 차이는 가중치 비율이 아니라 잣대 자체에서 온다."
        )
    else:
        parts.append("가중치를 바꾼 것도, 잣대를 바꾼 것도 모양 우선보다 좋아지지 않았다.")
    return " ".join(parts)


def build_report(runs: pd.DataFrame, horizon: int = 252, generated_at: str | None = None) -> str:
    """backtest_runs 형식의 표 → Markdown 문서 전체."""
    agg = aggregate(runs, horizon)
    v = verdict(agg)
    d = runs[runs["horizon"] == horizon]
    n_dates = int(d["as_of"].nunique())
    span = f"{d['as_of'].min().date()} ~ {d['as_of'].max().date()}" if n_dates else "(시점 없음)"
    stamp = generated_at or pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 백테스트 결과: 기준선 vs 모양 우선 vs 환경 우선",
        "",
        f"생성 {stamp} (`python -m stocker.backtest report`). 이 문서는 자동 생성된다. 승리 조건과 "
        "특징·가중치는 `docs/PLAN_PHASE6.md` 1·3절에 실험 전 고정했고, 결과를 보고 바꾸지 않았다.",
        "",
        f"- 평가 시점: 매월 말 거래일 {n_dates}개 ({span}). 각 시점은 그날까지의 데이터만 쓴다.",
        f"- 채점 horizon: {horizon}거래일(약 {round(horizon / 21)}개월). "
        f"구간 점수 α={ALPHA} (구간 밖 거리 × {2 / ALPHA:.0f}).",
        f"- 승리 조건: 전체 구간 점수 < 기준선 **그리고** 시대 4개 중 {MIN_ERAS_TO_WIN}개 이상 "
        "< 기준선.",
        "- 적합 기준(PHASE6_CONTRACT 23절)은 백테스트에도 적용: 닮은 과거가 없다고 판정된 시점은 "
        "기준선 분포로 채점한다.",
        "",
        "## 판정",
        "",
        f"**{v.conclusion}**",
        "",
        "| 모드 | 전체 구간 점수 | 기준선을 이긴 시대 | 승리 조건 |",
        "|---|---|---|---|",
    ]
    for m in (*CONTENDERS, *REFERENCE_MODES):
        if m in v.overall_is:
            status = "충족" if v.beats_baseline[m] else "미충족"
            if m in REFERENCE_MODES:
                status += " (참고 줄, 승자 대상 아님)"
            lines.append(
                f"| {_label(m)} | {_f(v.overall_is[m])} (기준선 {_f(v.overall_is['baseline'])}) | "
                f"{len(v.eras_won[m])}/4 ({', '.join(v.eras_won[m]) or '없음'}) | {status} |"
            )
    lines += ["", "## 쉬운 설명", "", _easy_explanation(agg, v, horizon), ""]
    lines += ["## 전체 기간", "", _table(agg, "전체"), ""]
    lines += ["## 시대별 구간 점수 (✓ = 기준선보다 낮음)", "", _era_matrix(agg), ""]
    for era in ERAS:
        lines += [f"## 시대 {era}", "", _table(agg, era), ""]
    lines += [
        "## 지표 설명",
        "",
        "- **구간 점수**: (p90 − p10) + (2/α)×(실제값이 구간 밖으로 벗어난 거리). "
        "낮을수록 좋다. 주 지표.",
        "- **적중률**: 실제값이 10~90% 구간 안에 든 비율. 80%에 가까울수록 좋다(너무 높으면 구간이 "
        "쓸데없이 넓은 것).",
        "- **방향 일치**: 중앙값의 부호와 실제값의 부호가 같은 비율.",
        "- **해당 없음 비율**: 적합 기준에서 ok 이상인 국면이 2개 미만(applicable=False)인 "
        "시점의 비율.",
        "- **기준선 대체 비율**: 그중 ok 이상이 0개여서 기준선 분포로 채점된 시점의 비율.",
        "- **평균 국면 수**: 분포를 만든 국면 수(n)의 평균. 기준선은 전체 과거 창의 수.",
        "",
    ]
    return "\n".join(lines)


def write_report(runs: pd.DataFrame, path: Path | str = DEFAULT_PATH, horizon: int = 252) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_report(runs, horizon), encoding="utf-8")
    return p


__all__ = ["DEFAULT_PATH", "build_report", "write_report"]
