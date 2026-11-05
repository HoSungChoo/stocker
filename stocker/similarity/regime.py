"""환경 우선 모드. 계약: docs/PHASE6_CONTRACT.md 24절, 특징·가중치는 docs/PLAN_PHASE6.md 3절(고정).

모양 우선(engine.score)은 "그래프 모양이 닮은 과거"를 찾는다. 이 모드는 "그래프를 둘러싼 환경이
닮은 과거"를 찾는다. 매칭 키는 여섯 그룹:

| 그룹(가중치) | 특징 |
|---|---|
| position 위치 (30) | 고점 대비 낙폭, 고점 이후 거래일 수, 직전 1년·2년 수익률, 60일 실현 변동성 |
| money 돈의 가치 (25) | 기준금리 z-score **와 수준 차이**, 6개월 변화, 실질금리(DFF − CPI YoY), |
|  | 10년물, 장단기 스프레드, 달러지수(2006~), M2 YoY, 연준 자산 YoY(2003~). 없는 시기는 제외 |
| policy 정책·이벤트 (10) | 최근 6개월 금리 방향(-1/0/+1), 6개월 내 정책·전쟁 이벤트 유무(0/1) |
| cycle 업황 (15) | WSTS 매출 YoY, YoY 6개월 변화 (발표 지연 반영) |
| narrative 내러티브 대리 (10) | VIX 수준, SOX의 200일선 대비 괴리 |
| shape 모양 (10) | 기존 DTW 모양 점수 (engine._price_layer 그대로) |

거리: 그룹별로 특징을 expanding z-score(각 날짜까지의 평균·표준편차, ±3σ 클리핑)로 표준화한 뒤
유클리드 거리 → 후보 전체에서의 백분위 → 그룹 가중합. 기준금리 "수준 차이"는 z-score와 별도로
원 단위(%p)를 2.0으로 나눈 차원으로 넣는다(적합 기준의 허용 폭 2.0%p를 1 단위로 삼음). 정책·이벤트
그룹은 값이 -1/0/1, 0/1이라 표준화 없이 그대로 거리를 잰다.

룩어헤드 금지: 입력은 as_of 이하로 자르고, 모든 통계는 expanding, 월간 발표 시리즈(CPI·M2·WSTS)는
발표 지연만큼 날짜를 늦춘다. forward 수익률만 표시용으로 as_of 이후를 쓴다.

용어: z-score = (값 − 그때까지의 평균) / 그때까지의 표준편차. "그 시대 기준으로 얼마나 특이한가".
실질금리 = 기준금리 − 물가상승률. 돈을 빌리는 진짜 비용.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from stocker.sim.leveraged import forward_returns
from stocker.similarity.config import REGIME_EVENT_KINDS, SimilarityConfig
from stocker.similarity.distance import euclidean_batch, to_score
from stocker.similarity.engine import (
    Match,
    SimilarityResult,
    _clean_close,
    _combine,
    _price_layer,
    _select_with_gap,
    _truncate,
    apply_fit,
)
from stocker.similarity.features import (
    WSTS_METRIC,
    _clean_series,
    build_cycle_features,
    build_price_features,
    expanding_zscore,
)
from stocker.similarity.fit import rate_direction

GROUPS = ("position", "money", "policy", "cycle", "narrative", "shape")
TRADING_DAYS = 252
RATE_LEVEL_SCALE_PP = 2.0  # 기준금리 수준 차이 차원의 단위 (%p)
EVENT_LOOKBACK_DAYS = 182  # "최근 6개월" (달력일)
MA_DAYS = 200
RATE_DIR_NUM = {"up": 1.0, "flat": 0.0, "down": -1.0}


# --------------------------------------------------------------------------- 정렬 도우미
def _align(s: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """임의 격자의 시리즈를 거래일 index로 ffill 정렬 (각 날짜 이하의 마지막 관측값)."""
    s = _clean_series(s)
    if s.empty:
        return pd.Series(np.nan, index=index)
    return s.reindex(s.index.union(index)).ffill().reindex(index)


def _lagged(s: pd.Series, lag_days: int) -> pd.Series:
    """월간 발표 시리즈의 날짜를 발표 지연만큼 늦춘다 (룩어헤드 방지)."""
    s = _clean_series(s)
    if lag_days > 0 and not s.empty:
        s = s.copy()
        s.index = s.index + pd.Timedelta(days=lag_days)
    return s


def _yoy_pct(s: pd.Series, periods: int) -> pd.Series:
    return (s / s.shift(periods) - 1.0) * 100.0


def _z(s: pd.Series, cfg: SimilarityConfig) -> pd.Series:
    """expanding z-score + ±clip σ 클리핑."""
    z = expanding_zscore(s, cfg.zscore_min_periods)
    return z.clip(-cfg.regime_clip_sigma, cfg.regime_clip_sigma)


def event_flag(
    events: pd.DataFrame | None, index: pd.DatetimeIndex, kinds=REGIME_EVENT_KINDS
) -> pd.Series:
    """각 거래일 t에 대해 (t-182일, t] 안에 정책·전쟁 이벤트가 있으면 1, 없으면 0.

    events: read_events 형식(date, kind, ...). None/빈 표면 모두 0 (이벤트 없음으로 간주).
    """
    if events is None or len(events) == 0 or "date" not in events:
        return pd.Series(0.0, index=index)
    ev = events
    if "kind" in ev and kinds:
        ev = ev[ev["kind"].isin(list(kinds))]
    dates = (
        pd.DatetimeIndex(pd.to_datetime(ev["date"])).tz_localize(None)
        if getattr(pd.DatetimeIndex(pd.to_datetime(ev["date"])), "tz", None) is not None
        else pd.DatetimeIndex(pd.to_datetime(ev["date"]))
    )
    dates = np.sort(dates.normalize().to_numpy())
    if len(dates) == 0:
        return pd.Series(0.0, index=index)
    t = index.to_numpy()
    upto = np.searchsorted(dates, t, side="right")
    before = np.searchsorted(dates, t - np.timedelta64(EVENT_LOOKBACK_DAYS, "D"), side="right")
    return pd.Series(((upto - before) > 0).astype("float64"), index=index)


# --------------------------------------------------------------------------- 특징
def build_regime_features(
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    events: pd.DataFrame | None,
    config: SimilarityConfig | None = None,
) -> dict[str, pd.DataFrame]:
    """그룹별 특징 표 {group: DataFrame(index=close의 거래일)}. shape 그룹은 engine이 계산한다.

    입력은 호출자가 as_of 이하로 자른다. 표준화된 그룹(position, money, cycle, narrative)의 값은
    expanding z-score(±3σ). policy 그룹은 원 값(-1/0/1, 0/1).
    """
    cfg = config or SimilarityConfig()
    s = _clean_close(close)
    idx = s.index
    macro = macro or {}
    cycle = cycle or {}
    out: dict[str, pd.DataFrame] = {}

    # --- position: 위치 ---
    px = s.to_numpy()
    running_max = np.maximum.accumulate(px)
    pos = np.arange(len(px))
    high_pos = np.maximum.accumulate(np.where(px >= running_max, pos, 0))
    position = pd.DataFrame(index=idx)
    position["drawdown"] = _z(pd.Series(px / running_max - 1.0, index=idx), cfg)
    position["dd_days"] = _z(pd.Series((pos - high_pos).astype("float64"), index=idx), cfg)
    position["ret_1y"] = _z(s / s.shift(TRADING_DAYS) - 1.0, cfg)
    position["ret_2y"] = _z(s / s.shift(2 * TRADING_DAYS) - 1.0, cfg)
    vol = np.log(s).diff().rolling(cfg.fit_vol_days).std(ddof=1) * math.sqrt(TRADING_DAYS)
    position["vol"] = _z(vol, cfg)
    out["position"] = position

    # --- money: 돈의 가치 ---
    money = pd.DataFrame(index=idx)
    dff = _align(macro["DFF"], idx) if "DFF" in macro else pd.Series(np.nan, index=idx)
    money["dff_z"] = _z(dff, cfg)
    money["dff_level"] = dff / RATE_LEVEL_SCALE_PP  # 수준 차이 (표준화 안 함)
    money["dff_chg6"] = _z(dff - dff.shift(cfg.change_days), cfg)
    cpi_yoy = pd.Series(np.nan, index=idx)
    if "CPIAUCSL" in macro:
        cpi = _lagged(macro["CPIAUCSL"], cfg.monthly_lag_days)
        cpi_yoy = _align(_yoy_pct(cpi, 12), idx)
    money["real_rate"] = _z(dff - cpi_yoy, cfg)
    for sid, col in (("DGS10", "dgs10"), ("T10Y2Y", "t10y2y"), ("DTWEXBGS", "dollar")):
        money[col] = _z(_align(macro[sid], idx), cfg) if sid in macro else np.nan
    if "M2SL" in macro:
        m2 = _lagged(macro["M2SL"], cfg.monthly_lag_days)
        money["m2_yoy"] = _z(_align(_yoy_pct(m2, 12), idx), cfg)
    else:
        money["m2_yoy"] = np.nan
    if "WALCL" in macro:
        w = _clean_series(macro["WALCL"])
        money["walcl_yoy"] = _z(_align(_yoy_pct(w, 52), idx), cfg)  # 주간 → 52주 전 대비
    else:
        money["walcl_yoy"] = np.nan
    out["money"] = money

    # --- policy: 정책·이벤트 (원 값) ---
    policy = pd.DataFrame(index=idx)
    direction = rate_direction(dff - dff.shift(cfg.change_days), cfg.fit_rate_dir_pp)
    policy["rate_dir"] = direction.map(RATE_DIR_NUM).astype("float64")
    policy["event_6m"] = event_flag(events, idx)
    out["policy"] = policy

    # --- cycle: 업황 ---
    cyc = pd.DataFrame(index=idx)
    cyc["wsts_yoy"] = np.nan
    cyc["wsts_yoy_chg6"] = np.nan
    if WSTS_METRIC in cycle:
        cfeats = build_cycle_features({WSTS_METRIC: cycle[WSTS_METRIC]})
        if not cfeats.empty:
            for col in ("wsts_yoy", "wsts_yoy_chg6"):
                lagged = _lagged(cfeats[col].dropna(), cfg.cycle_lag_days).loc[: idx[-1]]
                cyc[col] = _z(_align(lagged, idx), cfg)
    out["cycle"] = cyc

    # --- narrative: 내러티브 대리 ---
    nar = pd.DataFrame(index=idx)
    nar["vix"] = _z(_align(macro["VIXCLS"], idx), cfg) if "VIXCLS" in macro else np.nan
    nar["ma200_gap"] = _z(s / s.rolling(MA_DAYS).mean() - 1.0, cfg)
    out["narrative"] = nar
    return out


def _group_scores(
    feats: pd.DataFrame, anchor: pd.Timestamp, cand_dates: pd.DatetimeIndex
) -> np.ndarray | None:
    """그룹 특징 → 후보별 점수(0~1, 1이 가장 닮음). 기준일 값이 전부 NaN이면 None(그룹 결측)."""
    q = feats.loc[anchor].to_numpy(dtype="float64")
    if np.isnan(q).all():
        return None
    C = feats.loc[cand_dates].to_numpy(dtype="float64")
    dist = euclidean_batch(q, C)
    if np.isnan(dist).all():
        return None
    return to_score(dist)


# --------------------------------------------------------------------------- 점수
def score_regime(
    as_of,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    events: pd.DataFrame | None = None,
    config: SimilarityConfig | None = None,
) -> SimilarityResult:
    """환경 우선 모드. engine.score와 같은 SimilarityResult를 돌려준다 (순수 함수).

    Match.scores의 키는 GROUPS(position, money, policy, cycle, narrative, shape).
    """
    cfg = config or SimilarityConfig()
    requested = pd.Timestamp(as_of).normalize()
    full_close = _clean_close(close)
    hist = full_close.loc[:requested]
    if len(hist) < cfg.window + cfg.min_gap_days + 1:
        raise ValueError(
            f"as_of={requested.date()} 이전 데이터가 {len(hist)}개로 부족하다 "
            f"(최소 window+min_gap_days+1={cfg.window + cfg.min_gap_days + 1})"
        )
    anchor = hist.index[-1]
    anchor_pos = len(hist) - 1

    # 후보 창: 모양 우선과 같은 규칙 (창 완전, as_of보다 min_gap 이상 앞)
    pfeats = build_price_features(hist, cfg.window)
    query = pfeats.loc[anchor]
    end_pos = pd.Series(np.arange(len(hist)), index=hist.index)
    feat_pos = end_pos.loc[pfeats.index].to_numpy()
    cand_mask = feat_pos <= anchor_pos - cfg.min_gap_days
    cands = pfeats[cand_mask]
    cand_pos = feat_pos[cand_mask]
    n_cand = len(cands)
    if n_cand == 0:
        raise ValueError("min_gap_days를 만족하는 후보 창이 없다")

    macro_in = _truncate(macro, anchor)
    cycle_in = _truncate(cycle, anchor)
    events_in = None
    if events is not None and len(events) > 0 and "date" in events:
        ev_dates = pd.to_datetime(events["date"])
        events_in = events[ev_dates <= anchor]

    groups = build_regime_features(hist, macro_in, cycle_in, events_in, cfg)
    layer_scores: dict[str, np.ndarray] = {}
    layers_missing: list[str] = []
    for g in GROUPS:
        if g == "shape":
            layer_scores[g], _, _ = _price_layer(query, cands, cfg.window, cfg)
            continue
        sc = _group_scores(groups[g], anchor, cands.index)
        if sc is None:
            layers_missing.append(g)
        else:
            layer_scores[g] = sc

    present = {k: v for k, v in cfg.regime_weights.items() if k in layer_scores}
    total = sum(present.values())
    if total <= 0:
        raise ValueError("남아 있는 그룹의 가중치 합이 0이다")
    weights_used = {k: v / total for k, v in present.items()}
    combined = _combine(layer_scores, weights_used)
    chosen = _select_with_gap(combined, cand_pos, cfg.top_k, cfg.min_gap_days)

    fwd_all = forward_returns(full_close, cfg.horizons)
    matches: list[Match] = []
    for i in chosen:
        end = cands.index[i]
        fwd_row = fwd_all.loc[end]
        matches.append(
            Match(
                end_date=end,
                scores={k: float(v[i]) for k, v in layer_scores.items()},
                combined=float(combined[i]),
                forward={h: float(fwd_row[f"fwd_{h}"]) for h in cfg.horizons},
                start_date=hist.index[int(cand_pos[i]) - cfg.window + 1],
            )
        )
    fits, applicable, applicable_n, summary, source = apply_fit(
        matches, hist, macro_in, cycle_in, fwd_all, cfg
    )
    cand_table = pd.DataFrame({**layer_scores, "combined": combined}, index=cands.index)
    return SimilarityResult(
        as_of=anchor,
        config=cfg,
        matches=matches,
        n_candidates=n_cand,
        layers_missing=layers_missing,
        forward_summary=summary,
        weights_used=weights_used,
        requested_as_of=requested,
        candidates=cand_table,
        fit=fits,
        applicable=applicable,
        applicable_n=applicable_n,
        forward_summary_source=source,
        mode="regime",
    )


__all__ = ["GROUPS", "build_regime_features", "event_flag", "score_regime"]
