"""DB에서 입력을 읽어 engine.score를 돌린다. 이 패키지에서 DB를 만지는 유일한 파일."""

from __future__ import annotations

import pandas as pd

from stocker.similarity.config import CYCLE_METRICS, SimilarityConfig
from stocker.similarity.engine import SimilarityResult, score

SOX_SYMBOL = "^SOX"


def load_close(engine=None, symbol: str = SOX_SYMBOL) -> pd.Series:
    from stocker.db import read_prices

    df = read_prices(symbol, engine=engine)
    if df.empty:
        raise RuntimeError(f"DB에 {symbol} 시세가 없다. `python -m stocker.ingest prices` 먼저.")
    return df["close"].dropna().rename(symbol)


def load_macro(series_ids, engine=None) -> dict[str, pd.Series] | None:
    """macro_series에서 읽는다. 하나도 없으면 None (FRED 키가 없어 비어 있는 경우)."""
    from stocker.db import read_macro

    out: dict[str, pd.Series] = {}
    for sid in series_ids:
        s = read_macro(sid, engine=engine)
        if not s.empty:
            out[sid] = s
    return out or None


def load_cycle(metrics=CYCLE_METRICS, engine=None) -> dict[str, pd.Series] | None:
    """semi_cycle에서 읽는다. read_cycle이 없거나 테이블이 없으면 None."""
    try:
        from stocker.db import read_cycle
    except ImportError:
        return None
    out: dict[str, pd.Series] = {}
    for metric in metrics:
        try:
            s = read_cycle(metric, engine=engine)
        except Exception:  # noqa: BLE001  (테이블 없음 등 → 레이어 결측으로 처리)
            return None
        if not s.empty:
            out[metric] = s
    return out or None


def load_inputs(
    config: SimilarityConfig | None = None, engine=None
) -> tuple[pd.Series, dict[str, pd.Series] | None, dict[str, pd.Series] | None]:
    cfg = config or SimilarityConfig()
    if engine is None:
        from stocker.db import get_engine

        engine = get_engine()
    close = load_close(engine)
    macro = load_macro(cfg.macro_series, engine)
    cycle = load_cycle(engine=engine)
    return close, macro, cycle


def run(as_of, config: SimilarityConfig | None = None, engine=None) -> SimilarityResult:
    cfg = config or SimilarityConfig()
    close, macro, cycle = load_inputs(cfg, engine)
    return score(as_of, close, macro, cycle, cfg)


def walk_forward(
    as_of_dates: list, config: SimilarityConfig | None = None, engine=None
) -> pd.DataFrame:
    """여러 as_of에 대해 score를 돌려 forward_summary를 모은다.

    index=MultiIndex(as_of, horizon), columns=median,p10,p90,n_up,n_down,n,n_candidates.
    입력은 한 번만 읽는다.
    """
    cfg = config or SimilarityConfig()
    close, macro, cycle = load_inputs(cfg, engine)
    return walk_forward_from_inputs(as_of_dates, close, macro, cycle, cfg)


def walk_forward_from_inputs(
    as_of_dates: list,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    config: SimilarityConfig | None = None,
) -> pd.DataFrame:
    """walk_forward의 순수 함수 버전 (테스트·재사용용)."""
    cfg = config or SimilarityConfig()
    frames = []
    for d in as_of_dates:
        res = score(d, close, macro, cycle, cfg)
        fs = res.forward_summary.copy()
        fs["n_candidates"] = res.n_candidates
        fs["as_of"] = res.as_of
        frames.append(fs.reset_index())
    if not frames:
        return pd.DataFrame(
            columns=["median", "p10", "p90", "n_up", "n_down", "n", "n_candidates"]
        ).rename_axis(["as_of", "horizon"])
    out = pd.concat(frames, ignore_index=True)
    return out.set_index(["as_of", "horizon"])
