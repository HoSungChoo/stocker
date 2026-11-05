"""대시보드의 데이터 로딩. DB는 stocker.db·stocker.similarity.loaders를 통해서만 읽는다.

Streamlit 캐시(ttl 1시간)를 쓰지만 streamlit 런타임 밖에서 호출해도 동작한다.
사이드바의 '새로고침' 버튼은 `clear_cache()`를 부른다.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocker.similarity import SimilarityConfig, SimilarityResult, score
from stocker.similarity.config import DEFAULT_WEIGHTS
from stocker.similarity.loaders import load_inputs

TTL = 3600
SOXL_SYMBOL = "SOXL"
EVENT_COLUMNS = ["date", "kind", "title", "symbol", "scheduled", "source", "meta"]


def clear_cache() -> None:
    st.cache_data.clear()
    st.cache_resource.clear()


@st.cache_resource
def _engine():
    from stocker.db import get_engine

    return get_engine()


@st.cache_data(ttl=TTL, show_spinner="DB에서 시세·매크로·산업 사이클을 읽는 중…")
def load_all():
    """(SOX 종가, 매크로 dict|None, 사이클 dict|None). 엔진 입력 그대로."""
    return load_inputs(SimilarityConfig(), engine=_engine())


def make_config(
    window: int, top_k: int, weights: dict[str, float], cycle_lag_days: int
) -> SimilarityConfig:
    return SimilarityConfig(
        window=window,
        top_k=top_k,
        weights={**DEFAULT_WEIGHTS, **weights},
        cycle_lag_days=cycle_lag_days,
    )


@st.cache_data(ttl=TTL, show_spinner="유사 국면을 찾는 중…")
def run_similarity(
    as_of: str, window: int, top_k: int, weights: tuple, cycle_lag_days: int
) -> SimilarityResult:
    """loaders.run과 같은 일(load_inputs → score)을 캐시된 입력 위에서 한다."""
    close, macro, cycle = load_all()
    cfg = make_config(window, top_k, dict(weights), cycle_lag_days)
    return score(pd.Timestamp(as_of), close, macro, cycle, cfg)


@st.cache_data(ttl=TTL)
def load_events(start: str, end: str) -> pd.DataFrame:
    """이벤트 표. ImportError·테이블 없음·빈 결과는 모두 빈 DataFrame('이벤트 없음')."""
    try:
        from stocker.db import read_events
    except ImportError:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    try:
        df = read_events(start=start, end=end, engine=_engine())
    except Exception:  # noqa: BLE001  (events 테이블 없음 등)
        return pd.DataFrame(columns=EVENT_COLUMNS)
    if df is None or df.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    return df


@st.cache_data(ttl=TTL)
def load_rf() -> pd.Series | None:
    """DB의 DFF(연율, 퍼센트 단위). 없으면 None → 금융비용 0 가정."""
    from stocker.db import read_macro

    try:
        s = read_macro("DFF", engine=_engine())
    except Exception:  # noqa: BLE001
        return None
    return None if s.empty else s


@st.cache_data(ttl=TTL)
def load_soxl() -> pd.Series | None:
    """실제 SOXL 조정종가. 없으면 None."""
    from stocker.db import read_prices

    try:
        df = read_prices(SOXL_SYMBOL, engine=_engine())
    except Exception:  # noqa: BLE001
        return None
    s = df["adj_close"].dropna()
    return None if s.empty else s


@st.cache_data(ttl=300)
def load_positions() -> pd.DataFrame | None:
    """DB의 최신 스냅샷 중 보유 SOXL 행. 스냅샷이 없거나 테이블이 없으면 None."""
    from stocker.broker.positions import latest_positions, soxl_positions

    try:
        return soxl_positions(latest_positions(engine=_engine()))
    except Exception:  # noqa: BLE001
        return None


def broker_configured() -> bool:
    """TOSS_CLIENT_ID/SECRET이 이 프로세스에서 읽히는가 (API는 부르지 않는다)."""
    from stocker.broker import from_env

    return from_env() is not None


def refresh_positions() -> int:
    """토스에서 지금 보유 종목을 받아 스냅샷에 저장 (토큰은 DB 캐시 공유). 저장한 행 수."""
    from stocker.broker import from_env
    from stocker.broker.positions import snapshot_positions

    client = from_env(_engine())
    if client is None:
        raise RuntimeError("TOSS_CLIENT_ID/SECRET이 없다")
    n = len(snapshot_positions(client, engine=_engine()))
    load_positions.clear()
    return n
