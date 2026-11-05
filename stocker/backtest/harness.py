"""시점 생성과 모드별 실행. 계약: PHASE6_CONTRACT 25절.

룩어헤드 금지: 입력(종가·매크로·사이클·이벤트)은 한 번만 읽어 메모리에 두고, 각 as_of에서는
엔진(score/score_regime)이 as_of 이하로 잘라 쓴다. 기준선도 as_of까지의 종가만 쓴다.
`realized`(실제 이후 수익률)만 as_of 이후 데이터를 쓰며 채점에만 들어간다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import replace

import numpy as np
import pandas as pd

from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import _clean_close
from stocker.similarity.fit import baseline_forward_summary
from stocker.similarity.loaders import score_mode

logger = logging.getLogger(__name__)

# 참고 모드: 모양 우선 엔진 그대로, 가중치만 가격 0.1 / 매크로 0.6 / 업황 0.3.
# 환경 우선이 이기면 그것이 "가중치 비율" 때문인지 "잣대(특징) 자체" 때문인지 가르기 위한 줄.
SHAPE_ENV_WEIGHTS = {"price_shape": 0.1, "macro": 0.6, "semi_cycle": 0.3}
ALL_MODES = ("baseline", "shape", "shape_env", "regime")

RUN_COLUMNS = ["mode", "as_of", "horizon", "median", "p10", "p90", "n", "applicable", "realized"]
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
) -> list[dict]:
    rows = []
    for h in horizons:
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
                "applicable": applicable,
                "realized": _realized(close, anchor, int(h)),
            }
        )
    return rows


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
    if mode == "shape_env":
        cfg_env = replace(config, weights=dict(SHAPE_ENV_WEIGHTS), mode="shape")
        res = score_mode(as_of, close, macro, cycle, events, cfg_env, "shape")
    elif mode in ("shape", "regime"):
        res = score_mode(as_of, close, macro, cycle, events, config, mode)
    else:
        raise ValueError(f"알 수 없는 모드: {mode!r} (baseline|shape|shape_env|regime)")
    return _rows_from_summary(mode, res.as_of, res.forward_summary, hz, res.applicable, close)


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
    out = pd.DataFrame(rows, columns=RUN_COLUMNS)
    if not out.empty:
        out["as_of"] = pd.to_datetime(out["as_of"])
        out["applicable"] = out["applicable"].astype(object)
    return out


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


__all__ = [
    "ALL_MODES",
    "DEFAULT_END",
    "DEFAULT_START",
    "RUN_COLUMNS",
    "evaluate_point",
    "month_end_dates",
    "run_backtest",
    "run_backtest_from_inputs",
]
