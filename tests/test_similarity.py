"""stocker.similarity 테스트. 기본 실행은 네트워크·DB 없음(합성 데이터)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocker.similarity import (
    SimilarityConfig,
    build_cycle_features,
    build_macro_features,
    build_price_features,
    score,
)
from stocker.similarity.distance import (
    cosine_distance_batch,
    dtw_distance,
    dtw_distance_batch,
    rank_percentile,
    to_score,
)
from stocker.similarity.features import expanding_zscore
from stocker.similarity.loaders import walk_forward_from_inputs

WINDOW = 120


# --------------------------------------------------------------------------- helpers
def _walk(rng, n: int, vol: float, drift: float = 0.0) -> np.ndarray:
    """일일 로그수익률 배열."""
    return rng.normal(drift, vol, n)


def _pattern(n: int = WINDOW) -> np.ndarray:
    """뚜렷한 V자: 앞 절반 -20%, 뒤 절반 +75% (로그수익률, 결정적)."""
    half = n // 2
    down = np.full(half, np.log(0.8) / half)
    up = np.full(n - half, np.log(1.75) / (n - half))
    wiggle = 0.004 * np.sin(np.arange(n) * 0.7)
    return np.concatenate([down, up]) + wiggle


def _synthetic_close(seed: int = 0) -> tuple[pd.Series, int]:
    """[랜덤 A 1000] + [패턴 P] + [랜덤 B 1500] + [패턴 P]. 반환: (close, 첫 P의 끝 위치)."""
    rng = np.random.default_rng(seed)
    p = _pattern()
    rets = np.concatenate([_walk(rng, 1000, 0.003), p, _walk(rng, 1500, 0.003), p])
    idx = pd.bdate_range("1995-01-02", periods=len(rets))
    close = pd.Series(100.0 * np.exp(np.cumsum(rets)), index=idx, name="^SOX")
    planted_end = 1000 + WINDOW - 1
    return close, planted_end


def _random_close(seed: int, n: int = 3000) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("1994-01-03", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n))), index=idx)


def _macro_dict(index: pd.DatetimeIndex, seed: int) -> dict[str, pd.Series]:
    rng = np.random.default_rng(seed)
    return {
        "DFF": pd.Series(np.cumsum(rng.normal(0, 0.02, len(index))) + 3, index=index),
        "VIXCLS": pd.Series(20 + np.cumsum(rng.normal(0, 0.3, len(index))), index=index),
    }


def _cycle_dict(start: str, months: int, seed: int) -> dict[str, pd.Series]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=months, freq="ME")
    sales = 10_000 * np.exp(np.cumsum(rng.normal(0.004, 0.04, months)))
    return {"wsts_sales_world_usd_m": pd.Series(sales, index=idx)}


def _dtw_reference(a, b) -> float:
    """밴드 없는 교과서식 O(nm) DTW (|a_i-b_j| 비용)."""
    n, m = len(a), len(b)
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = abs(a[i - 1] - b[j - 1]) + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return float(D[n, m])


# --------------------------------------------------------------------------- distance
def test_dtw_hand_computed():
    # a=[0,0,1], b=[0,1,1]: 점별 L1 거리는 1이지만, 시간을 늘려 맞추면 0.
    assert dtw_distance([0, 0, 1], [0, 1, 1], band_frac=1.0) == 0.0
    # 밴드 0 → 대각선만 허용 → L1 거리와 같다.
    assert dtw_distance([0, 0, 1], [0, 1, 1], band_frac=0.0) == 1.0
    # 길이가 다른 쌍: [1,2,3] vs [1,2,2,3] → 0
    assert dtw_distance([1, 2, 3], [1, 2, 2, 3], band_frac=1.0) == 0.0
    # 손계산: [1,3,4] vs [1,2,4]: 최적 경로 비용 1
    assert dtw_distance([1, 3, 4], [1, 2, 4], band_frac=1.0) == 1.0


def test_dtw_matches_reference_and_batch():
    rng = np.random.default_rng(3)
    q = rng.normal(size=12)
    C = rng.normal(size=(7, 12))
    got = dtw_distance_batch(q, C, band_frac=1.0)
    ref = np.array([_dtw_reference(q, c) for c in C])
    np.testing.assert_allclose(got, ref, rtol=1e-12)
    # 밴드가 좁아지면 거리는 커지거나 같다
    narrow = dtw_distance_batch(q, C, band_frac=0.1)
    assert np.all(narrow >= got - 1e-12)


def test_percentile_scores():
    np.testing.assert_allclose(to_score(np.array([3.0, 1.0, 2.0, np.nan])), [0.0, 1.0, 0.5, np.nan])
    np.testing.assert_allclose(rank_percentile(np.array([5.0, 5.0, 5.0])), [0.5, 0.5, 0.5])
    np.testing.assert_allclose(to_score(np.array([2.0])), [1.0])
    assert np.isnan(to_score(np.array([np.nan, np.nan]))).all()


def test_cosine_masks_missing_dims():
    q = np.array([1.0, 0.0, np.nan])
    C = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, 5.0], [np.nan, np.nan, 1.0]])
    d = cosine_distance_batch(q, C)
    assert d[0] == pytest.approx(0.0)
    assert d[1] == pytest.approx(1.0)
    assert np.isnan(d[2])


# --------------------------------------------------------------------------- features
def test_price_features_shape_and_drawdown():
    close = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0, 2.0], index=pd.bdate_range("2020-01-01", periods=6))
    f = build_price_features(close, window=3)
    assert list(f.index) == list(close.index[2:])
    shape = f[[c for c in f.columns if c.startswith("shape_")]].to_numpy()
    np.testing.assert_allclose(shape.mean(axis=1), 0.0, atol=1e-12)
    np.testing.assert_allclose(shape.std(axis=1), 1.0, atol=1e-12)
    np.testing.assert_allclose(f["drawdown"].to_numpy(), [0.0, 2 / 3 - 1, 1 / 3 - 1, 2 / 3 - 1])
    assert f["dd_days"].tolist() == [0, 1, 2, 3]


def test_expanding_zscore_uses_only_past():
    idx = pd.bdate_range("2000-01-03", periods=400)
    rng = np.random.default_rng(7)
    base = pd.Series(np.cumsum(rng.normal(size=400)), index=idx)
    changed = base.copy()
    changed.iloc[300:] += 50.0  # 뒤쪽만 바꾼다
    z1 = expanding_zscore(base, 60)
    z2 = expanding_zscore(changed, 60)
    pd.testing.assert_series_equal(z1.iloc[:300], z2.iloc[:300])
    assert not np.allclose(z1.iloc[300:], z2.iloc[300:])

    f1 = build_macro_features({"X": base}, change_days=20)
    f2 = build_macro_features({"X": changed}, change_days=20)
    cutoff = idx[299]
    pd.testing.assert_frame_equal(f1.loc[:cutoff], f2.loc[:cutoff])
    assert {"X_level_z", "X_chg_z"} == set(f1.columns)


def test_cycle_features_yoy():
    idx = pd.date_range("2000-01-31", periods=30, freq="ME")
    sales = pd.Series(np.arange(1, 31, dtype=float) * 100, index=idx)
    f = build_cycle_features({"wsts_sales_world_usd_m": sales})
    assert f["wsts_yoy"].loc["2001-01-31"] == pytest.approx((13 / 1 - 1) * 100)
    assert f["wsts_yoy_chg6"].loc["2001-07-31"] == pytest.approx(
        f["wsts_yoy"].loc["2001-07-31"] - f["wsts_yoy"].loc["2001-01-31"]
    )
    assert build_cycle_features({}).empty


# --------------------------------------------------------------------------- engine (계약 8.5)
def test_synthetic_pattern_found_first():
    close, planted_end = _synthetic_close()
    cfg = SimilarityConfig(window=WINDOW)
    res = score(close.index[-1], close, None, None, cfg)
    assert res.layers_missing == ["macro", "semi_cycle"]
    top = res.matches[0]
    assert top.end_date == close.index[planted_end]
    assert top.scores["price_shape"] == pytest.approx(1.0)
    assert top.combined == pytest.approx(1.0)
    assert top.start_date == close.index[planted_end - WINDOW + 1]
    # forward는 실제 종가 비율과 같다 (표시용)
    exp = close.iloc[planted_end + 21] / close.iloc[planted_end] - 1
    assert top.forward[21] == pytest.approx(exp)
    # 요약 표
    assert list(res.forward_summary.index) == list(cfg.horizons)
    assert res.forward_summary.loc[21, "n"] == len(res.matches)


def test_min_gap_between_matches_and_as_of():
    close = _random_close(11, n=4000)
    cfg = SimilarityConfig(window=60, top_k=8, min_gap_days=250)
    res = score(close.index[-1], close, None, None, cfg)
    assert len(res.matches) == 8
    pos = [close.index.get_loc(m.end_date) for m in res.matches]
    anchor = len(close) - 1
    for i, p in enumerate(pos):
        assert anchor - p >= cfg.min_gap_days
        for q in pos[:i]:
            assert abs(p - q) >= cfg.min_gap_days
    # combined 내림차순
    comb = [m.combined for m in res.matches]
    assert comb == sorted(comb, reverse=True)


def test_no_lookahead_truncated_inputs_identical():
    close = _random_close(5, n=3000)
    macro = _macro_dict(close.index, 9)
    cycle = _cycle_dict("1994-01-31", 150, 2)
    as_of = close.index[2400]
    cfg = SimilarityConfig(window=120, top_k=5)

    full = score(as_of, close, macro, cycle, cfg)
    cut_macro = {k: v.loc[:as_of] for k, v in macro.items()}
    cut_cycle = {k: v.loc[:as_of] for k, v in cycle.items()}
    cut = score(as_of, close.loc[:as_of], cut_macro, cut_cycle, cfg)

    assert full.layers_missing == [] and cut.layers_missing == []
    assert full.n_candidates == cut.n_candidates
    assert [m.end_date for m in full.matches] == [m.end_date for m in cut.matches]
    for a, b in zip(full.matches, cut.matches, strict=True):
        assert a.combined == pytest.approx(b.combined, abs=1e-12)
        for layer in a.scores:
            assert a.scores[layer] == pytest.approx(b.scores[layer], abs=1e-12, nan_ok=True)
    # 잘린 입력에서는 as_of 이후가 없으므로 긴 horizon의 forward는 NaN이 될 수 있다 (표시용)
    assert all(np.isnan(v) or np.isfinite(v) for m in cut.matches for v in m.forward.values())


def test_layers_missing_macro_only():
    close = _random_close(21, n=2500)
    cycle = _cycle_dict("1994-01-31", 130, 4)
    cfg = SimilarityConfig(window=60)
    res = score(close.index[-1], close, None, cycle, cfg)
    assert res.layers_missing == ["macro"]
    assert set(res.weights_used) == {"price_shape", "semi_cycle"}
    assert sum(res.weights_used.values()) == pytest.approx(1.0)
    assert res.weights_used["price_shape"] == pytest.approx(0.5 / 0.7)
    for m in res.matches:
        assert "macro" not in m.scores
        assert 0.0 <= m.combined <= 1.0

    # 빈 dict도 결측으로 취급
    res2 = score(close.index[-1], close, {}, cycle, cfg)
    assert res2.layers_missing == ["macro"]


def test_as_of_not_trading_day_uses_previous():
    close = _random_close(3, n=2000)
    sat = close.index[-1] + pd.Timedelta(days=1)  # 마지막 거래일 다음 날 (비거래일)
    res = score(sat, close, None, None, SimilarityConfig(window=60))
    assert res.as_of == close.index[-1]
    assert res.requested_as_of == sat.normalize()


def test_insufficient_history_raises():
    close = _random_close(3, n=300)
    with pytest.raises(ValueError):
        score(close.index[-1], close, None, None, SimilarityConfig(window=120, min_gap_days=250))


def test_walk_forward_from_inputs():
    close = _random_close(8, n=3000)
    cfg = SimilarityConfig(window=60, top_k=3)
    dates = [close.index[1500], close.index[2500]]
    out = walk_forward_from_inputs(dates, close, None, None, cfg)
    assert out.index.names == ["as_of", "horizon"]
    assert len(out) == 2 * len(cfg.horizons)
    assert set(out.columns) >= {"median", "p10", "p90", "n_up", "n_down", "n", "n_candidates"}
    assert (out["n"] <= 3).all()


def test_config_validation():
    with pytest.raises(ValueError):
        SimilarityConfig(weights={"price_shape": 0.0, "macro": 0.0, "semi_cycle": 0.0})
    with pytest.raises(ValueError):
        SimilarityConfig(window=5)


# --------------------------------------------------------------------------- DB
@pytest.mark.db
def test_run_against_db(engine):
    from stocker.similarity import run

    res = run("2022-10-10", SimilarityConfig(), engine=engine)
    assert res.n_candidates > 5000
    assert len(res.matches) == 5
    assert res.as_of == pd.Timestamp("2022-10-10")


@pytest.mark.db
def test_no_lookahead_against_db_2018(engine):
    """계약 8.5: DB 전체 vs as_of 이후를 잘라낸 입력 → 동일 결과."""
    from stocker.similarity.loaders import load_inputs

    as_of = pd.Timestamp("2018-12-20")
    cfg = SimilarityConfig()
    close, macro, cycle = load_inputs(cfg, engine)
    full = score(as_of, close, macro, cycle, cfg)
    cut = score(
        as_of,
        close.loc[:as_of],
        None if macro is None else {k: v.loc[:as_of] for k, v in macro.items()},
        None if cycle is None else {k: v.loc[:as_of] for k, v in cycle.items()},
        cfg,
    )
    assert full.n_candidates == cut.n_candidates
    assert full.layers_missing == cut.layers_missing
    assert [m.end_date for m in full.matches] == [m.end_date for m in cut.matches]
    for a, b in zip(full.matches, cut.matches, strict=True):
        assert a.combined == pytest.approx(b.combined, abs=1e-12)
