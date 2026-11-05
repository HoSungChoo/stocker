"""체크리스트 신호 규칙 9개 (계약 PHASE6C 30.1절). 규칙은 데이터(RULES)이고 평가는 순수 함수다.

- `evaluate(date, series, close)`는 date 이하 데이터만 쓴다(룩어헤드 금지). 월간·분기 시리즈는
  발표 지연을 고려해 날짜를 45일 뒤로 밀어(WSTS와 같은 방식) 그 날짜 이전에 알 수 있던 값만 쓴다.
- 시리즈가 없거나 date 이전 값이 없으면, 또는 마지막 값이 너무 오래됐으면 `on=None`(데이터 없음).
- DB 접근은 `load_inputs`/`evaluate_db`에만 있다. 입력은 1시간 캐시한다.
"""

from __future__ import annotations

import operator
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

SOX_SYMBOL = "^SOX"
LAG_DAYS = 45  # 월간·분기 시리즈 발표 지연
DAILY_STALE_DAYS = 10  # 일간 시리즈의 마지막 값이 이보다 오래되면 데이터 없음 (휴장·결측 여유)
LAGGED_STALE_DAYS = 100  # 지연 반영 후에도 이보다 오래됐으면 데이터 없음 (수집이 끊긴 경우)
CACHE_TTL = 3600.0

# 날짜를 밀어서 쓰는 월간·분기 시리즈
LAGGED_SERIES = frozenset({"SHILLER_CAPE", "SHILLER_EP", "IPO_FIRSTDAY", "CAPEX_IPS_GDP"})

# 그룹 id → 화면 소제목 (프론트도 같은 id를 쓴다)
GROUPS = {"bottom": "바닥", "bubble": "버블 끝", "heat": "과열", "crisis": "위기"}

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "<": operator.lt,
}


@dataclass(frozen=True)
class Signal:
    id: str
    group: str
    label: str
    on: bool | None  # None = 데이터 없음
    value: float | None
    threshold: float
    unit: str
    op: str  # 켜짐 조건 "값 {op} 임계값" (호버 문구용)


# 값 함수: (date, 보이는 시리즈, date 이하 종가) -> float | None. 입력은 이미 date 이하로 잘려 있다.
ValueFn = Callable[[pd.Timestamp, dict[str, pd.Series], pd.Series], float | None]


@dataclass(frozen=True)
class Rule:
    id: str
    group: str
    label: str
    needs: tuple[str, ...]  # 필요한 macro 시리즈 id (종가는 별도)
    threshold: float
    op: str
    unit: str
    value_fn: ValueFn
    needs_close: bool = False


def _last(s: pd.Series | None) -> float | None:
    if s is None or s.empty:
        return None
    return float(s.iloc[-1])


def _drop_from_60d_high(date, s, close):
    """DGS10 현재값 − 최근 60거래일(관측 60개) 최고. 60개가 안 되면 None."""
    x = s["DGS10"]
    if len(x) < 60:
        return None
    w = x.iloc[-60:]
    return float(w.iloc[-1] - w.max())


def _fed_change_30d(date, s, close):
    """최근 30일 DFF 변화(%p): 현재값 − 30일 전 이전 마지막 값."""
    x = s["DFF"]
    past = x.loc[: date - pd.Timedelta(days=30)]
    if past.empty:
        return None
    return float(x.iloc[-1] - past.iloc[-1])


def _yield_spread(date, s, close):
    """이익수익률(E/P) − 10년물 금리, %p 단위. 일드 스프레드 = SHILLER_EP − DGS10/100."""
    ep, y10 = _last(s["SHILLER_EP"]), _last(s["DGS10"])
    if ep is None or y10 is None:
        return None
    return ep * 100 - y10


def _sox_return_1y(date, s, close):
    """SOX 252거래일 수익률(%)."""
    if len(close) < 253:
        return None
    return float((close.iloc[-1] / close.iloc[-253] - 1) * 100)


def _latest(sid: str) -> ValueFn:
    return lambda date, s, close: _last(s[sid])


RULES: tuple[Rule, ...] = (
    Rule(
        "bottom_10y_turn",
        "bottom",
        "10년물 금리 꺾임",
        ("DGS10",),
        -0.4,
        "<=",
        "%p",
        _drop_from_60d_high,
    ),
    Rule(
        "bottom_fed_cut", "bottom", "연준 금리 인하", ("DFF",), -0.20, "<=", "%p", _fed_change_30d
    ),
    Rule(
        "bubble_inversion",
        "bubble",
        "장단기 금리 역전",
        ("T10Y2Y",),
        0.0,
        "<",
        "%p",
        _latest("T10Y2Y"),
    ),
    Rule(
        "bubble_yield_spread",
        "bubble",
        "일드 스프레드",
        ("SHILLER_EP", "DGS10"),
        -2.7,
        "<=",
        "%p",
        _yield_spread,
    ),
    Rule(
        "bubble_ipo",
        "bubble",
        "공모주 첫날 수익률",
        ("IPO_FIRSTDAY",),
        50.0,
        ">=",
        "%",
        _latest("IPO_FIRSTDAY"),
    ),
    Rule(
        "heat_cape",
        "heat",
        "주가 고평가(CAPE)",
        ("SHILLER_CAPE",),
        35.0,
        ">=",
        "배",
        _latest("SHILLER_CAPE"),
    ),
    Rule(
        "heat_sox_1y",
        "heat",
        "SOX 1년 급등",
        (),
        100.0,
        ">=",
        "%",
        _sox_return_1y,
        needs_close=True,
    ),
    Rule(
        "crisis_hy",
        "crisis",
        "하이일드 스프레드",
        ("BAMLH0A0HYM2",),
        7.0,
        ">=",
        "%p",
        _latest("BAMLH0A0HYM2"),
    ),
    Rule(
        "crisis_ccc",
        "crisis",
        "CCC 이하 스프레드",
        ("BAMLH0A3HYC",),
        12.0,
        ">=",
        "%p",
        _latest("BAMLH0A3HYC"),
    ),
)

NEEDED_SERIES = tuple(dict.fromkeys(sid for r in RULES for sid in r.needs))


def _visible(sid: str, s: pd.Series | None, date: pd.Timestamp) -> pd.Series:
    """date 시점에 알 수 있던 값만 남긴다. 지연 시리즈는 인덱스를 45일 뒤로 민다.

    마지막 값이 너무 오래됐으면 빈 시리즈(데이터 없음).
    """
    if s is None or s.empty:
        return pd.Series(dtype="float64")
    s = s.dropna().sort_index()
    lagged = sid in LAGGED_SERIES
    if lagged:
        s = s.copy()
        s.index = s.index + pd.Timedelta(days=LAG_DAYS)
    s = s.loc[:date]
    if s.empty:
        return s
    limit = LAGGED_STALE_DAYS if lagged else DAILY_STALE_DAYS
    if (date - s.index[-1]).days > limit:
        return s.iloc[0:0]
    return s


def evaluate(date, series: dict[str, pd.Series], close: pd.Series) -> list[Signal]:
    """date 시점의 신호 9개. date 이하 데이터만 쓴다."""
    date = pd.Timestamp(date)
    visible = {sid: _visible(sid, series.get(sid), date) for sid in NEEDED_SERIES}
    # 종가는 지연이 없다. 데이터 공백 판정은 일간 시리즈와 같은 기준.
    close_vis = (
        close.dropna().sort_index().loc[:date] if close is not None else pd.Series(dtype=float)
    )
    out: list[Signal] = []
    for r in RULES:
        value: float | None = None
        ready = all(not visible[sid].empty for sid in r.needs) and (
            not r.needs_close or not close_vis.empty
        )
        if r.needs_close and ready and (date - close_vis.index[-1]).days > DAILY_STALE_DAYS:
            ready = False
        if ready:
            value = r.value_fn(date, visible, close_vis)
        if value is not None:
            value = round(float(value), 9)  # 부동소수 잡음이 경계값 판정을 흔들지 않게
        on = None if value is None else bool(_OPS[r.op](value, r.threshold))
        out.append(Signal(r.id, r.group, r.label, on, value, r.threshold, r.unit, r.op))
    return out


# ----------------------------------------------------------------- DB (stocker/db/ 경유)

_lock = threading.Lock()
_cache: dict[int, tuple[float, tuple[dict[str, pd.Series], pd.Series]]] = {}


def load_inputs(engine=None) -> tuple[dict[str, pd.Series], pd.Series]:
    """(macro 시리즈 dict, SOX 종가). DB에서 읽는다(캐시 없음). 없는 시리즈는 dict에서 빠진다."""
    from stocker.db import get_engine, read_macro, read_prices

    engine = engine or get_engine()
    series: dict[str, pd.Series] = {}
    for sid in NEEDED_SERIES:
        s = read_macro(sid, engine=engine)
        if not s.empty:
            series[sid] = s
    close = read_prices(SOX_SYMBOL, engine=engine)["close"].dropna()
    return series, close


def evaluate_db(date, engine=None) -> list[Signal]:
    """DB에서 읽은 입력(1시간 캐시)으로 evaluate."""
    key = id(engine)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit is None or hit[0] <= now:
        data = load_inputs(engine)
        with _lock:
            _cache[key] = (now + CACHE_TTL, data)
    else:
        data = hit[1]
    return evaluate(date, *data)
