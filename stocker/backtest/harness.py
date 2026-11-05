"""시점 생성과 모드별 실행. 계약: PHASE6_CONTRACT 25절, preset은 PLAN_PHASE6B 2절.

룩어헤드 금지: 입력(종가·매크로·사이클·이벤트)은 한 번만 읽어 메모리에 두고, 각 as_of에서는
엔진(score/score_regime)에 **as_of까지 잘린 종가**를 넘긴다. 그래서 as_of와 horizon보다 가까운
과거 창의 이후 수익률(as_of 시점엔 아직 모르는 값)은 NaN이 되어 분포에 들어가지 않는다
(min_gap이 horizon보다 작은 k=20·30에서 특히 중요). 기준선도 as_of까지의 종가만 쓴다.
`realized`(실제 이후 수익률)만 전체 종가에서 계산하며 채점에만 들어간다.
어느 horizon에서 ok 이상인 창의 결과를 하나도 알 수 없으면 그 horizon 행은 기준선 분포로 채점하고
applicable=False로 둔다(계약 23절 "ok 이상 0개 → 기준선"의 horizon별 확장).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import replace

import numpy as np
import pandas as pd

from stocker.backtest.presets import EFFECTIVE_GAP, Arm, effective_count, get_preset
from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import SimilarityResult, _clean_close
from stocker.similarity.fit import baseline_forward_summary, is_ok
from stocker.similarity.loaders import score_mode

logger = logging.getLogger(__name__)

# 참고 모드: 모양 우선 엔진 그대로, 가중치만 가격 0.1 / 매크로 0.6 / 업황 0.3.
# 환경 우선이 이기면 그것이 "가중치 비율" 때문인지 "잣대(특징) 자체" 때문인지 가르기 위한 줄.
SHAPE_ENV_WEIGHTS = {"price_shape": 0.1, "macro": 0.6, "semi_cycle": 0.3}
ALL_MODES = ("baseline", "shape", "shape_env", "regime")

RUN_COLUMNS = [
    "mode",
    "as_of",
    "horizon",
    "median",
    "p10",
    "p90",
    "n",
    "applicable",
    "realized",
    "n_eff",
]
# preset 실행이 덧붙이는 컬럼 (DB의 같은 이름 컬럼에 저장)
PRESET_COLUMNS = ["preset", "top_k", "min_gap"]
DEFAULT_START = "1999-01"
DEFAULT_END = "2025-10"


def month_end_dates(index: pd.DatetimeIndex, start: str, end: str) -> list[pd.Timestamp]:
    """index(거래일) 안에서 start~end(YYYY-MM, 양끝 포함) 각 달의 마지막 거래일."""
    idx = pd.DatetimeIndex(index).sort_values()
    s = pd.Series(idx, index=idx)
    lo = pd.Period(start, freq="M")
    hi = pd.Period(end, freq="M")
    per = s.index.to_period("M")
    s = s[(per >= lo) & (per <= hi)]
    if s.empty:
        return []
    return list(s.groupby(s.index.to_period("M")).last())


def _realized(close: pd.Series, anchor: pd.Timestamp, horizon: int) -> float:
    pos = close.index.get_loc(anchor)
    if pos + horizon >= len(close):
        return float("nan")
    return float(close.iloc[pos + horizon] / close.iloc[pos] - 1.0)


def _rows_from_summary(
    mode: str,
    anchor: pd.Timestamp,
    summary: pd.DataFrame,
    horizons: Iterable[int],
    applicable,
    close: pd.Series,
    n_eff: dict[int, float] | None = None,
    fallback: pd.DataFrame | None = None,
) -> list[dict]:
    """horizon마다 1행. fallback이 있으면 n=0인 horizon은 그 분포(기준선)로 바꾸고
    applicable=False."""
    rows = []
    n_eff = n_eff or {}
    for h in horizons:
        app = applicable
        if (
            fallback is not None
            and h in fallback.index
            and (h not in summary.index or int(summary.loc[h, "n"]) == 0)
        ):
            summary, app = fallback, False
        if h in summary.index:
            r = summary.loc[h]
            med, p10, p90, n = r["median"], r["p10"], r["p90"], int(r["n"])
        else:
            med, p10, p90, n = np.nan, np.nan, np.nan, 0
        rows.append(
            {
                "mode": mode,
                "as_of": anchor,
                "horizon": int(h),
                "median": float(med),
                "p10": float(p10),
                "p90": float(p90),
                "n": n,
                "applicable": app,
                "realized": _realized(close, anchor, int(h)),
                "n_eff": n_eff.get(int(h), float("nan")),
            }
        )
    return rows


def distribution_dates(res: SimilarityResult, horizon: int | None = None) -> pd.DatetimeIndex:
    """분포(forward_summary)를 만든 창의 끝 날짜들.

    - 적합 판정이 됐으면 ok 이상인 창만 (apply_fit 규칙 그대로).
    - 판정 불가(applicable=None)면 상위 K 전체.
    - 기준선으로 대체됐으면(source="baseline") 빈 인덱스.
    - horizon을 주면 그 horizon의 이후 수익률을 아는(NaN이 아닌) 창만.
    """
    if res.forward_summary_source == "baseline":
        return pd.DatetimeIndex([])
    if res.applicable is None or not res.fit:
        pairs = [(m, True) for m in res.matches]
    else:
        pairs = [(m, is_ok(f)) for m, f in zip(res.matches, res.fit, strict=True)]
    return pd.DatetimeIndex(
        [
            m.end_date
            for m, ok in pairs
            if ok and (horizon is None or not math.isnan(m.forward.get(horizon, float("nan"))))
        ]
    )


def effective_sample_size(
    res: SimilarityResult,
    close: pd.Series,
    horizon: int | None = None,
    gap: int = EFFECTIVE_GAP,
) -> float:
    """실질 표본 수: 분포를 만든 창 중 서로 `gap` 거래일 이상 떨어진 창의 수. 기준선 대체면 NaN."""
    if res.forward_summary_source == "baseline":
        return float("nan")
    dates = distribution_dates(res, horizon)
    pos = close.index.get_indexer(dates)
    return float(effective_count(pos[pos >= 0], gap))


def evaluate_point(
    mode: str,
    as_of,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    events: pd.DataFrame | None,
    config: SimilarityConfig,
    horizons: Iterable[int] | None = None,
) -> list[dict]:
    """한 시점·한 모드의 결과 행들(horizon마다 1행). close는 정리된(_clean_close) 전체 종가."""
    hz = tuple(horizons) if horizons is not None else config.horizons
    requested = pd.Timestamp(as_of).normalize()
    hist = close.loc[:requested]
    if hist.empty:
        raise ValueError(f"{requested.date()} 이전 시세가 없다")
    anchor = hist.index[-1]
    if mode == "baseline":
        summary = baseline_forward_summary(hist, hz)
        return _rows_from_summary("baseline", anchor, summary, hz, None, close)
    # 엔진에는 as_of까지 잘린 종가만 넘긴다: 아직 결과를 모르는 창의 이후 수익률이 분포에 섞이지
    # 않도록 (모듈 docstring). 매칭·적합 판정은 어차피 as_of 이하만 쓰므로 결과가 같다.
    if mode == "shape_env":
        cfg_env = replace(config, weights=dict(SHAPE_ENV_WEIGHTS), mode="shape")
        res = score_mode(as_of, hist, macro, cycle, events, cfg_env, "shape")
    elif mode in ("shape", "regime"):
        res = score_mode(as_of, hist, macro, cycle, events, config, mode)
    else:
        raise ValueError(f"알 수 없는 모드: {mode!r} (baseline|shape|shape_env|regime)")
    n_eff = {int(h): effective_sample_size(res, close, int(h)) for h in hz}
    fallback = None
    if res.forward_summary_source != "baseline" and res.matches:
        known = res.forward_summary.reindex(list(hz))["n"].fillna(0)
        if (known == 0).any():
            fallback = baseline_forward_summary(hist, hz)
    return _rows_from_summary(
        mode, res.as_of, res.forward_summary, hz, res.applicable, close, n_eff, fallback
    )


def arm_config(arm: Arm, config: SimilarityConfig) -> SimilarityConfig:
    """arm의 (top_k, min_gap)만 바꾼 설정. 나머지(가중치·적합 기준)는 그대로."""
    if arm.mode == "baseline":
        return config
    return replace(config, top_k=arm.top_k, min_gap_days=arm.min_gap, mode=arm.mode)


def evaluate_arm(
    arm: Arm,
    as_of,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    events: pd.DataFrame | None,
    config: SimilarityConfig,
    horizons: Iterable[int] | None = None,
    preset: str | None = None,
) -> list[dict]:
    """한 시점·한 arm. evaluate_point에 arm 설정을 넣고 mode 컬럼을 arm 이름으로 바꾼다."""
    rows = evaluate_point(
        arm.mode, as_of, close, macro, cycle, events, arm_config(arm, config), horizons
    )
    for r in rows:
        r["mode"] = arm.name
        r["preset"] = preset
        r["top_k"] = arm.top_k
        r["min_gap"] = arm.min_gap
    return rows


def _finish(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(rows, columns=columns)
    if not out.empty:
        out["as_of"] = pd.to_datetime(out["as_of"])
        out["applicable"] = out["applicable"].astype(object)
    return out


def run_backtest_from_inputs(
    dates: Iterable,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    events: pd.DataFrame | None,
    modes: Iterable[str] = ("baseline", "shape", "regime"),
    config: SimilarityConfig | None = None,
    horizons: Iterable[int] | None = None,
    progress: Callable[[int, int, float], None] | None = None,
) -> pd.DataFrame:
    """순수 함수 버전. 반환 columns=RUN_COLUMNS. 실패한 시점은 경고 후 건너뛴다."""
    cfg = config or SimilarityConfig()
    full = _clean_close(close)
    dates = list(dates)
    modes = list(modes)
    rows: list[dict] = []
    t0 = time.perf_counter()
    for i, d in enumerate(dates, 1):
        for mode in modes:
            try:
                rows.extend(evaluate_point(mode, d, full, macro, cycle, events, cfg, horizons))
            except ValueError as exc:
                logger.warning("%s %s 건너뜀: %s", mode, pd.Timestamp(d).date(), exc)
        if progress is not None:
            progress(i, len(dates), time.perf_counter() - t0)
    return _finish(rows, RUN_COLUMNS)


def run_preset_from_inputs(
    arms: Iterable[Arm],
    dates: Iterable,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    events: pd.DataFrame | None,
    config: SimilarityConfig | None = None,
    horizons: Iterable[int] | None = None,
    progress: Callable[[int, int, float], None] | None = None,
    preset: str | None = None,
) -> pd.DataFrame:
    """preset(arm 목록)의 순수 함수 버전. 반환 columns=RUN_COLUMNS+PRESET_COLUMNS.

    모든 arm이 같은 시점 목록을 쓴다. 실패한 (arm, 시점)은 경고 후 건너뛴다.
    """
    cfg = config or SimilarityConfig()
    full = _clean_close(close)
    dates = list(dates)
    arms = list(arms)
    rows: list[dict] = []
    t0 = time.perf_counter()
    for i, d in enumerate(dates, 1):
        for arm in arms:
            try:
                rows.extend(evaluate_arm(arm, d, full, macro, cycle, events, cfg, horizons, preset))
            except ValueError as exc:
                logger.warning("%s %s 건너뜀: %s", arm.name, pd.Timestamp(d).date(), exc)
        if progress is not None:
            progress(i, len(dates), time.perf_counter() - t0)
    return _finish(rows, RUN_COLUMNS + PRESET_COLUMNS)


def run_backtest(
    modes: Iterable[str] = ("baseline", "shape", "regime"),
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    config: SimilarityConfig | None = None,
    horizons: Iterable[int] | None = None,
    engine=None,
    progress: Callable[[int, int, float], None] | None = None,
) -> pd.DataFrame:
    """DB에서 입력을 한 번 읽어 매월 말 거래일마다 모드별로 평가한다 (저장은 호출자가)."""
    from stocker.similarity.loaders import load_all_inputs

    cfg = config or SimilarityConfig()
    close, macro, cycle, events = load_all_inputs(cfg, engine)
    dates = month_end_dates(_clean_close(close).index, start, end)
    return run_backtest_from_inputs(
        dates, close, macro, cycle, events, modes, cfg, horizons, progress
    )


def run_preset(
    preset: str,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    config: SimilarityConfig | None = None,
    horizons: Iterable[int] | None = None,
    engine=None,
    progress: Callable[[int, int, float], None] | None = None,
) -> pd.DataFrame:
    """DB에서 입력을 한 번 읽어 preset의 모든 arm을 같은 시점들에서 평가한다 (저장은 호출자가)."""
    from stocker.similarity.loaders import load_all_inputs

    cfg = config or SimilarityConfig()
    arms = get_preset(preset)
    close, macro, cycle, events = load_all_inputs(cfg, engine)
    dates = month_end_dates(_clean_close(close).index, start, end)
    return run_preset_from_inputs(
        arms, dates, close, macro, cycle, events, cfg, horizons, progress, preset
    )


__all__ = [
    "ALL_MODES",
    "DEFAULT_END",
    "DEFAULT_START",
    "PRESET_COLUMNS",
    "RUN_COLUMNS",
    "arm_config",
    "distribution_dates",
    "effective_sample_size",
    "evaluate_arm",
    "evaluate_point",
    "month_end_dates",
    "run_backtest",
    "run_backtest_from_inputs",
    "run_preset",
    "run_preset_from_inputs",
]
