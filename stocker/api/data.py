"""API가 호출하는 데이터·계산 함수. 라우트는 이 모듈만 통해 DB와 엔진에 닿는다.

- 함수는 모두 모듈 수준이라 테스트에서 monkeypatch로 바꿔 끼울 수 있다.
- 엔진 입력(SOX 종가·매크로·사이클)은 한 번 읽어 1시간 캐시하고, 유사도 결과는 쿼리별로 캐시한다.
  (`stocker.web.data`와 같은 방식: loaders.load_inputs → engine.score. loaders.run은 호출마다
  DB를 다시 읽어 2초가 걸리므로 쓰지 않는다. 계산 자체는 같은 함수다.)
- 선택 모듈(stocker.events, read_event_impact)은 함수 안에서 lazy import하고, 없거나 테이블이
  없으면 빈 결과로 처리한다.
"""

from __future__ import annotations

import datetime as dt
import inspect
import logging

import pandas as pd

from stocker.api.cache import TTLCache, make_key
from stocker.api.common import CommonQuery
from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import SimilarityResult, score
from stocker.similarity.explain import Explanation, explain

logger = logging.getLogger(__name__)

SOX_SYMBOL = "^SOX"
EVENT_COLUMNS = ["date", "kind", "title", "symbol", "scheduled", "source", "meta"]

inputs_cache = TTLCache()
result_cache = TTLCache()
_engine = None


def engine():
    global _engine
    if _engine is None:
        from stocker.db import get_engine

        _engine = get_engine()
    return _engine


def clear_caches() -> None:
    inputs_cache.clear()
    result_cache.clear()


# ----------------------------------------------------------------- 엔진 입력


def load_inputs() -> tuple[pd.Series, dict | None, dict | None]:
    """(SOX 종가, 매크로 dict|None, 사이클 dict|None). DB에서 읽는다 (캐시 없음)."""
    from stocker.similarity.loaders import load_inputs as _load

    return _load(SimilarityConfig(), engine=engine())


def inputs() -> tuple[pd.Series, dict | None, dict | None]:
    return inputs_cache.get_or_set(make_key("inputs"), load_inputs)


def last_trading_day() -> pd.Timestamp:
    close, _, _ = inputs()
    return close.index[-1]


def layers_missing_static() -> list[str]:
    """입력 자체가 없어 빠지는 레이어 (health용. 점수를 돌리지 않는다)."""
    _, macro, cycle = inputs()
    out = []
    if not macro:
        out.append("macro")
    if not cycle:
        out.append("semi_cycle")
    return out


def resolve_as_of(q: CommonQuery) -> pd.Timestamp:
    return pd.Timestamp(q.as_of) if q.as_of else last_trading_day()


def similarity(q: CommonQuery) -> SimilarityResult:
    """쿼리별 캐시된 유사도 결과."""

    def compute():
        if q.mode:
            res = _run_with_mode(q)
            if res is not None:
                return res
        close, macro, cycle = inputs()
        return score(resolve_as_of(q), close, macro, cycle, q.config())

    return result_cache.get_or_set(make_key("similarity", q.key()), compute)


def _run_with_mode(q: CommonQuery) -> SimilarityResult | None:
    """loaders.run이 mode를 받으면(세션 R, 계약 24절) 그것으로 계산한다. 아니면 None → 기본 경로."""
    from stocker.similarity import loaders

    if "mode" not in inspect.signature(loaders.run).parameters:
        return None
    return loaders.run(resolve_as_of(q), q.config(), engine=engine(), mode=q.mode)


def fit_for(q: CommonQuery, end) -> object | None:
    """상위 국면 중 끝 날짜가 end인 것의 적합 판정(FitCheck). 엔진이 fit을 안 내면 None."""
    try:
        res = similarity(q)
    except ValueError:
        return None
    fits = getattr(res, "fit", None)
    if not fits:
        return None
    end_ts = pd.Timestamp(end)
    for m, f in zip(res.matches, fits, strict=False):
        if pd.Timestamp(m.end_date) == end_ts:
            return f
    return None


def explanation(q: CommonQuery, end: str) -> Explanation:
    def compute():
        close, macro, cycle = inputs()
        return explain(resolve_as_of(q), end, close, macro, cycle, q.config())

    return result_cache.get_or_set(make_key("explain", {**q.key(), "end": end}), compute)


def close_series() -> pd.Series:
    return inputs()[0]


def rf_series() -> pd.Series | None:
    """DFF(연율 %, 금융비용용). 매크로 입력에 있으면 그것을 쓴다."""
    _, macro, _ = inputs()
    if macro and "DFF" in macro and not macro["DFF"].empty:
        return macro["DFF"]
    return None


# ----------------------------------------------------------------- 이벤트


def events(start, end, kinds: list[str] | None = None) -> pd.DataFrame:
    """read_events. 테이블 없음 등은 빈 표."""
    from stocker.db import read_events

    try:
        df = read_events(start=start, end=end, kinds=kinds, engine=engine())
    except Exception as exc:  # noqa: BLE001
        logger.warning("이벤트 조회 실패: %s", exc.__class__.__name__)
        return pd.DataFrame(columns=EVENT_COLUMNS)
    if df is None or df.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    return df


def event_impact(limit: int, kinds: list[str] | None) -> pd.DataFrame:
    """stocker.db.read_event_impact (세션 B). 없으면 빈 표."""
    try:
        from stocker.db import read_event_impact
    except ImportError:
        return pd.DataFrame()
    try:
        df = read_event_impact(limit=limit, kinds=kinds, engine=engine())
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_impact 조회 실패: %s", exc.__class__.__name__)
        return pd.DataFrame()
    return pd.DataFrame() if df is None else df


def event_by_id(event_id: int) -> dict | None:
    """id로 이벤트 한 건 (id, date, kind, title). read_event_impact 조인 결과에서 찾는다.

    (stocker.db에 id 단건 조회가 없어 영향 표 전체에서 찾는다. 계약 제안: read_event(id).)
    """
    df = event_impact(limit=100_000, kinds=None)
    if df.empty or "id" not in df:
        return None
    hit = df[df["id"] == event_id]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {"id": int(r["id"]), "date": r["date"], "kind": r["kind"], "title": r["title"]}


def event_path(event_id: int, days: int) -> pd.Series | None:
    """stocker.events.event_path (세션 B). 모듈이 없거나 실패하면 None."""
    try:
        from stocker.events import event_path as _path
    except ImportError:
        return None
    try:
        return _path(event_id, days=days, engine=engine())
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_path 실패: %s", exc.__class__.__name__)
        return None


# ----------------------------------------------------------------- 포지션 · 보고서


def latest_positions() -> pd.DataFrame | None:
    """최신 스냅샷(최근 4일 이내) 전체 행. 없으면 None."""
    from stocker.broker.positions import latest_positions as _latest

    try:
        df = _latest(engine=engine())
    except Exception as exc:  # noqa: BLE001
        logger.warning("포지션 조회 실패: %s", exc.__class__.__name__)
        return None
    return None if df is None or df.empty else df


def leveraged_returns(close, matches, window, rf) -> dict[int, list[float]]:
    from stocker.broker.positions import leveraged_returns as _lev

    return _lev(close, matches, window, rf)


def apply_distribution(position_value, forward_summary, leverage_paths) -> pd.DataFrame:
    from stocker.broker.positions import apply_distribution as _apply

    return _apply(position_value, forward_summary, leverage_paths)


def report_run(window: int = 0) -> dict | None:
    """report_runs의 최신 행 (window=0은 전체 보고서 Markdown)."""
    from stocker.db import read_report_run

    try:
        return read_report_run(window=window, engine=engine())
    except Exception as exc:  # noqa: BLE001
        logger.warning("보고서 조회 실패: %s", exc.__class__.__name__)
        return None


def today() -> dt.date:
    return dt.date.today()
