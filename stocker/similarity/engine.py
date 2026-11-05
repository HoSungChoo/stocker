"""유사도 엔진 핵심(순수 함수). 계약: docs/DATA_CONTRACT.md 8절.

score()는 DataFrame/Series만 받는다. DB 접근은 loaders.py에만 있다.

룩어헤드 금지 구현 요약:
1. 점수 계산용 입력은 모두 `as_of`(실제로는 as_of 이하의 마지막 거래일) 이하로 자른다.
2. 후보 끝 날짜는 `as_of`보다 `min_gap_days` 거래일 이상 앞선 날짜만.
3. z-score는 expanding, 전고점은 각 날짜까지의 최고가 (features.py).
4. forward 수익률은 결과 표시용으로만 계산하고 점수에는 쓰지 않는다.
   이 값만 as_of 이후 데이터를 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stocker.sim.leveraged import forward_returns
from stocker.similarity.config import SimilarityConfig
from stocker.similarity.distance import (
    cosine_distance_batch,
    dtw_distance_batch,
    euclidean_batch,
    rank_percentile,
    standardized_euclidean_batch,
    to_score,
)
from stocker.similarity.features import (
    DD_DAYS_COL,
    DRAWDOWN_COL,
    build_cycle_features,
    build_macro_features,
    build_price_features,
    shape_columns,
)
from stocker.similarity.fit import (
    FitCheck,
    baseline_forward_summary,
    build_fit_features,
    check_fit,
    features_at,
    is_ok,
)

LAYERS = ("price_shape", "macro", "semi_cycle")
TRADING_DAYS = 252


@dataclass
class Match:
    end_date: pd.Timestamp
    scores: dict[str, float]  # 레이어별 0~1 (NaN 가능)
    combined: float
    forward: dict[int, float]  # horizon → 이후 SOX 수익률. 미래 데이터 부족이면 NaN
    start_date: pd.Timestamp | None = None  # 창 시작 날짜 (표시용, 계약 외 추가)


@dataclass
class SimilarityResult:
    as_of: pd.Timestamp  # 실제 기준 거래일 (요청일 이하의 마지막 거래일)
    config: SimilarityConfig
    matches: list[Match]  # top_k, combined 내림차순, min_gap 적용
    n_candidates: int
    layers_missing: list[str]
    forward_summary: pd.DataFrame  # index=horizon, columns=median,p10,p90,n_up,n_down,n
    weights_used: dict[str, float] = field(default_factory=dict)  # 재정규화된 가중치 (계약 외 추가)
    requested_as_of: pd.Timestamp | None = None  # 호출자가 넘긴 날짜 (계약 외 추가)
    # 후보 전체의 점수표 (계약 외 추가, explain용). index=end_date,
    # columns=price_shape,macro,semi_cycle(없는 레이어는 없음),combined,euclid_rank,dtw_rank
    # euclid_rank: 창 벡터 유클리드 거리의 순위(1이 가장 가까움, 전 후보).
    # dtw_rank: DTW 거리의 순위(유클리드 사전 필터 안에서만; 밖이면 NaN).
    candidates: pd.DataFrame | None = None
    # --- 적합 기준 (PHASE6_CONTRACT 23절). 기본값은 "판정 안 함"(기존 호출자 호환) ---
    fit: list[FitCheck] = field(default_factory=list)  # matches와 같은 순서
    # True/False = 판정 결과. None = 판정 불가(금리·업황 입력이 없어 조건 5개를 다 검사할 수 없음).
    applicable: bool | None = None
    applicable_n: int = 0  # 상위 K 중 ok 이상인 수
    # "matches": 상위 K(ok 이상)로 계산. "baseline": 전체 과거 분포로 대체(applicable=False).
    forward_summary_source: str = "matches"
    mode: str = "shape"


def _clean_close(close: pd.Series) -> pd.Series:
    s = pd.Series(close, copy=True).astype("float64")
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    s = s.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.dropna()


def _truncate(series: dict[str, pd.Series] | None, as_of: pd.Timestamp) -> dict[str, pd.Series]:
    """dict의 각 Series를 as_of 이하로 자르고 빈 것은 버린다."""
    if not series:
        return {}
    out: dict[str, pd.Series] = {}
    for k, v in series.items():
        if v is None or len(v) == 0:
            continue
        s = pd.Series(v).dropna()
        if s.empty:
            continue
        idx = pd.DatetimeIndex(s.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        s.index = idx
        s = s.sort_index()
        s = s.loc[:as_of]
        if not s.empty:
            out[k] = s
    return out


def _price_layer(
    query: pd.Series, cands: pd.DataFrame, window: int, cfg: SimilarityConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """price_shape 점수 (후보 수,). 1이 가장 닮음. 반환: (점수, 유클리드 순위, DTW 순위).

    순위는 1부터(1이 가장 가까움). DTW 순위는 사전 필터에 든 후보만 있고 나머지는 NaN.

    1) 창 벡터 유클리드 거리로 전 후보 정렬 → 상위 `prefilter`개 선별
    2) 선별된 후보는 DTW 거리와 낙폭 특징 절대차를 (1-w, w)로 가중 합산해 재정렬
       (두 성분은 선별 집합 안에서 0~1 백분위로 바꾼 뒤 합산: 단위가 다르기 때문)
    3) 최종 순위 = [선별 후보(2)의 순서] + [나머지 후보(1)의 순서] → 전 후보 백분위 점수
    """
    cols = shape_columns(window)
    q_shape = query[cols].to_numpy(dtype="float64")
    c_shape = cands[cols].to_numpy(dtype="float64")
    n = len(cands)
    eucl = euclidean_batch(q_shape, c_shape)
    order = np.argsort(eucl, kind="stable")
    p = min(cfg.prefilter, n)
    top = order[:p]
    rest = order[p:]

    dtw = dtw_distance_batch(q_shape, c_shape[top], cfg.band_frac)
    dd = cands[DRAWDOWN_COL].to_numpy(dtype="float64")[top]
    dd_days = cands[DD_DAYS_COL].to_numpy(dtype="float64")[top]
    dd_dist = (
        np.abs(dd - float(query[DRAWDOWN_COL]))
        + np.abs(dd_days - float(query[DD_DAYS_COL])) / TRADING_DAYS
    )
    w = cfg.drawdown_weight
    inner = (1.0 - w) * rank_percentile(dtw) + w * rank_percentile(dd_dist)
    # 동점(예: 둘 다 0)이면 DTW 원값, 그다음 유클리드 순으로 안정 정렬
    top_sorted = top[np.lexsort((eucl[top], dtw, inner))]

    rank = np.empty(n, dtype="float64")
    final_order = np.concatenate([top_sorted, rest])
    rank[final_order] = np.arange(n, dtype="float64")
    score = 1.0 - (rank / (n - 1.0) if n > 1 else rank)

    eucl_rank = np.empty(n, dtype="float64")
    eucl_rank[order] = np.arange(1, n + 1, dtype="float64")
    dtw_rank = np.full(n, np.nan)
    dtw_rank[top[np.argsort(dtw, kind="stable")]] = np.arange(1, len(top) + 1, dtype="float64")
    return score, eucl_rank, dtw_rank


def _layer_scores_from_features(
    feats: pd.DataFrame,
    trading_index: pd.DatetimeIndex,
    anchor: pd.Timestamp,
    cand_dates: pd.DatetimeIndex,
    metric: str,
) -> np.ndarray | None:
    """영업일/월말 격자의 특징을 거래일로 ffill 정렬한 뒤 거리 → 점수. 전부 NaN이면 None."""
    if feats is None or feats.empty:
        return None
    aligned = feats.reindex(feats.index.union(trading_index)).ffill().reindex(trading_index)
    q = aligned.loc[anchor].to_numpy(dtype="float64")
    C = aligned.loc[cand_dates].to_numpy(dtype="float64")
    if np.isnan(q).all():
        return None
    if metric == "cosine":
        dist = cosine_distance_batch(q, C)
    else:
        # 표준화 스케일은 후보 집합 + 기준점(모두 as_of 이하)의 표준편차
        pool = np.vstack([C, q[None, :]])
        with np.errstate(invalid="ignore"):
            scale = np.nanstd(pool, axis=0, ddof=1)
        dist = standardized_euclidean_batch(q, C, scale)
    if np.isnan(dist).all():
        return None
    return to_score(dist)


def _combine(scores: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    """가중 평균. 후보별로 NaN인 레이어는 빼고 남은 가중치로 재정규화한다."""
    n = len(next(iter(scores.values())))
    num = np.zeros(n)
    den = np.zeros(n)
    for layer, s in scores.items():
        w = weights.get(layer, 0.0)
        ok = ~np.isnan(s)
        num[ok] += w * s[ok]
        den[ok] += w
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    return np.where(den > 0, out, np.nan)


def _select_with_gap(
    combined: np.ndarray, cand_pos: np.ndarray, top_k: int, min_gap: int
) -> list[int]:
    """combined 내림차순으로 훑으며, 이미 뽑힌 것과 거래일 간격이 min_gap 이상인 것만 뽑는다."""
    order = np.argsort(-np.where(np.isnan(combined), -np.inf, combined), kind="stable")
    chosen: list[int] = []
    for i in order:
        if np.isnan(combined[i]):
            break
        if all(abs(int(cand_pos[i]) - int(cand_pos[j])) >= min_gap for j in chosen):
            chosen.append(int(i))
            if len(chosen) >= top_k:
                break
    return chosen


def apply_fit(
    matches: list[Match],
    hist: pd.Series,
    macro_in: dict[str, pd.Series],
    cycle_in: dict[str, pd.Series],
    fwd_all: pd.DataFrame,
    cfg: SimilarityConfig,
) -> tuple[list[FitCheck], bool | None, int, pd.DataFrame, str]:
    """적합 기준(23절)을 상위 K에 적용한다. 반환: (fit, applicable, applicable_n, summary, source).

    - fit: 국면마다 조건 5개 검사 결과(matches 순서).
    - 기준일(now) 쪽에 금리·업황 값이 없어 조건을 다 검사할 수 없으면 판정 불가: applicable=None,
      summary는 상위 K 전체(기존 동작), source="matches".
    - ok 이상이 fit_min_applicable개 이상 → applicable=True, summary는 ok 이상만.
    - ok 이상이 있지만 그보다 적으면 applicable=False, summary는 그래도 ok 이상만
      (n이 작다는 사실을 숨기지 않는다).
    - ok 이상이 하나도 없으면 applicable=False, summary는 기준선(전체 과거), source="baseline".
    """
    anchor = hist.index[-1]
    feats = build_fit_features(hist, macro_in, cycle_in, cfg)
    now = features_at(feats, anchor)
    fits = [check_fit(features_at(feats, m.end_date), now, cfg) for m in matches]
    chosen_dates = pd.DatetimeIndex([m.end_date for m in matches])
    if not now.complete:
        return (
            fits,
            None,
            sum(1 for f in fits if is_ok(f)),
            (summarize_forward(fwd_all.loc[chosen_dates], cfg.horizons)),
            "matches",
        )
    ok_dates = pd.DatetimeIndex(
        [m.end_date for m, f in zip(matches, fits, strict=True) if is_ok(f)]
    )
    n_ok = len(ok_dates)
    if n_ok == 0:
        return fits, False, 0, baseline_forward_summary(hist, cfg.horizons), "baseline"
    summary = summarize_forward(fwd_all.loc[ok_dates], cfg.horizons)
    return fits, n_ok >= cfg.fit_min_applicable, n_ok, summary, "matches"


def summarize_forward(fwd: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    """index=horizon, columns=median,p10,p90,n_up,n_down,n. NaN은 제외하고 집계."""
    rows = []
    for h in horizons:
        col = f"fwd_{h}"
        vals = fwd[col].dropna().to_numpy() if col in fwd else np.array([])
        if len(vals) == 0:
            rows.append((h, np.nan, np.nan, np.nan, 0, 0, 0))
            continue
        rows.append(
            (
                h,
                float(np.median(vals)),
                float(np.percentile(vals, 10)),
                float(np.percentile(vals, 90)),
                int((vals > 0).sum()),
                int((vals < 0).sum()),
                int(len(vals)),
            )
        )
    out = pd.DataFrame(rows, columns=["horizon", "median", "p10", "p90", "n_up", "n_down", "n"])
    return out.set_index("horizon")


def score(
    as_of,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    config: SimilarityConfig | None = None,
) -> SimilarityResult:
    """as_of 시점의 SOX 국면과 닮은 과거 국면을 찾는다 (순수 함수).

    close: SOX 종가 전체(as_of 이후를 포함해도 된다 — 점수에는 as_of 이하만, 이후 구간은
           표시용 forward 수익률에만 쓴다).
    macro: {FRED id: Series}. None/빈 dict면 레이어 결측.
    cycle: {metric: Series}. None/빈 dict면 레이어 결측.
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

    # --- price_shape ---
    feats = build_price_features(hist, cfg.window)
    query = feats.loc[anchor]
    end_pos = pd.Series(np.arange(len(hist)), index=hist.index)
    feat_pos = end_pos.loc[feats.index].to_numpy()
    cand_mask = feat_pos <= anchor_pos - cfg.min_gap_days
    cands = feats[cand_mask]
    cand_pos = feat_pos[cand_mask]
    n_cand = len(cands)
    if n_cand == 0:
        raise ValueError("min_gap_days를 만족하는 후보 창이 없다")

    layer_scores: dict[str, np.ndarray] = {}
    layer_scores["price_shape"], eucl_rank, dtw_rank = _price_layer(query, cands, cfg.window, cfg)

    # --- macro ---
    layers_missing: list[str] = []
    macro_in = _truncate(macro, anchor)
    macro_scores = None
    if macro_in:
        mfeats = build_macro_features(macro_in, cfg.change_days, cfg.zscore_min_periods)
        macro_scores = _layer_scores_from_features(
            mfeats, hist.index, anchor, cands.index, "cosine"
        )
    if macro_scores is None:
        layers_missing.append("macro")
    else:
        layer_scores["macro"] = macro_scores

    # --- semi_cycle ---
    cycle_scores = None
    cycle_in = _truncate(cycle, anchor)
    if cycle_in:
        cfeats = build_cycle_features(cycle_in)
        if not cfeats.empty and cfg.cycle_lag_days > 0:
            cfeats = cfeats.copy()
            cfeats.index = cfeats.index + pd.Timedelta(days=cfg.cycle_lag_days)
            cfeats = cfeats.loc[:anchor]
        cycle_scores = _layer_scores_from_features(
            cfeats, hist.index, anchor, cands.index, "std_euclid"
        )
    if cycle_scores is None:
        layers_missing.append("semi_cycle")
    else:
        layer_scores["semi_cycle"] = cycle_scores

    # --- 종합 ---
    present = {k: v for k, v in cfg.weights.items() if k in layer_scores}
    total = sum(present.values())
    if total <= 0:
        raise ValueError("남아 있는 레이어의 가중치 합이 0이다")
    weights_used = {k: v / total for k, v in present.items()}
    combined = _combine(layer_scores, weights_used)
    chosen = _select_with_gap(combined, cand_pos, cfg.top_k, cfg.min_gap_days)

    # --- forward (표시용; 점수에 쓰지 않음) ---
    fwd_all = forward_returns(full_close, cfg.horizons)
    matches: list[Match] = []
    for i in chosen:
        end = cands.index[i]
        start_idx = int(cand_pos[i]) - cfg.window + 1
        fwd_row = fwd_all.loc[end]
        matches.append(
            Match(
                end_date=end,
                scores={k: float(v[i]) for k, v in layer_scores.items()},
                combined=float(combined[i]),
                forward={h: float(fwd_row[f"fwd_{h}"]) for h in cfg.horizons},
                start_date=hist.index[start_idx],
            )
        )
    fits, applicable, applicable_n, summary, source = apply_fit(
        matches, hist, macro_in, cycle_in, fwd_all, cfg
    )
    cand_table = pd.DataFrame(
        {**layer_scores, "combined": combined, "euclid_rank": eucl_rank, "dtw_rank": dtw_rank},
        index=cands.index,
    )

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
        mode="shape",
    )
