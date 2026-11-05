"""환경 우선 모드(regime.py) 테스트. 계약: PHASE6_CONTRACT 24절. 네트워크·DB 없음."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocker.similarity.config import REGIME_WEIGHTS, SimilarityConfig
from stocker.similarity.loaders import score_mode
from stocker.similarity.regime import GROUPS, build_regime_features, event_flag, score_regime

WINDOW = 60


def _inputs(seed: int = 0, n: int = 4000):
    """합성 입력: 랜덤 가격 + 환경 시리즈(금리·VIX·CPI 등) + WSTS + 이벤트."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("1994-01-03", periods=n)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n))), index=idx)
    macro = {
        "DFF": pd.Series(np.clip(3 + np.cumsum(rng.normal(0, 0.01, n)), 0, None), index=idx),
        "DGS10": pd.Series(np.clip(4 + np.cumsum(rng.normal(0, 0.01, n)), 0, None), index=idx),
        "T10Y2Y": pd.Series(np.cumsum(rng.normal(0, 0.01, n)), index=idx),
        "VIXCLS": pd.Series(np.clip(20 + np.cumsum(rng.normal(0, 0.3, n)), 5, None), index=idx),
        "DTWEXBGS": pd.Series(100 + np.cumsum(rng.normal(0, 0.2, n)), index=idx),
    }
    months = pd.date_range("1993-01-31", periods=200, freq="ME")
    macro["CPIAUCSL"] = pd.Series(
        100 * np.exp(np.cumsum(rng.normal(0.002, 0.002, 200))), index=months
    )
    macro["M2SL"] = pd.Series(3000 * np.exp(np.cumsum(rng.normal(0.004, 0.004, 200))), index=months)
    weeks = pd.date_range("2002-12-18", idx[-1], freq="W-WED")
    macro["WALCL"] = pd.Series(
        700_000 * np.exp(np.cumsum(rng.normal(0.001, 0.01, len(weeks)))), index=weeks
    )
    cycle = {
        "wsts_sales_world_usd_m": pd.Series(
            10_000 * np.exp(np.cumsum(rng.normal(0.004, 0.04, 200))), index=months
        )
    }
    events = pd.DataFrame(
        {
            "date": pd.to_datetime(["1997-07-02", "2001-09-11", "2008-09-15"]),
            "kind": ["policy", "war", "policy"],
            "title": ["a", "b", "c"],
        }
    )
    return close, macro, cycle, events


# --------------------------------------------------------------------------- 특징
def test_event_flag_six_month_window():
    idx = pd.bdate_range("2001-01-01", "2002-12-31")
    ev = pd.DataFrame(
        {"date": pd.to_datetime(["2001-09-11", "2002-06-01"]), "kind": ["war", "cpi"]}
    )
    f = event_flag(ev, idx)
    assert f.loc["2001-09-10"] == 0.0
    assert f.loc["2001-09-11"] == 1.0
    assert f.loc["2002-03-11"] == 1.0  # 181일 뒤
    assert f.loc["2002-03-13"] == 0.0  # 183일 뒤
    assert f.loc["2002-06-03"] == 0.0  # cpi는 정책·전쟁이 아니다
    assert (event_flag(None, idx) == 0).all()


def test_regime_features_groups_and_no_lookahead():
    close, macro, cycle, events = _inputs()
    cfg = SimilarityConfig(window=WINDOW)
    cut = close.index[3000]
    full = build_regime_features(close, macro, cycle, events, cfg)
    assert set(full) == set(GROUPS) - {"shape"}
    assert list(full["money"].columns) == [
        "dff_z",
        "dff_level",
        "dff_chg6",
        "real_rate",
        "dgs10",
        "t10y2y",
        "dollar",
        "m2_yoy",
        "walcl_yoy",
    ]
    # 표준화 그룹은 ±3σ 안
    for g in ("position", "cycle", "narrative"):
        v = full[g].to_numpy()
        assert np.nanmax(np.abs(v)) <= 3.0 + 1e-9
    # 수준 차이 차원은 표준화하지 않는다: DFF / 2.0
    pd.testing.assert_series_equal(
        full["money"]["dff_level"],
        macro["DFF"].reindex(close.index).ffill() / 2.0,
        check_names=False,
    )
    # 룩어헤드: 입력을 cut에서 잘라도 cut 이하의 특징은 같다
    part = build_regime_features(
        close.loc[:cut],
        {k: v.loc[:cut] for k, v in macro.items()},
        {k: v.loc[:cut] for k, v in cycle.items()},
        events[events["date"] <= cut],
        cfg,
    )
    for g in part:
        pd.testing.assert_frame_equal(full[g].loc[:cut], part[g])


def test_missing_dimensions_before_series_start():
    """WALCL(2002-12~), 달러지수가 없는 시기는 그 차원만 NaN이고 그룹은 살아 있다."""
    close, macro, cycle, events = _inputs()
    f = build_regime_features(close, macro, cycle, events, SimilarityConfig(window=WINDOW))
    money = f["money"]
    early = money.loc["1996-06-03"]
    assert np.isnan(early["walcl_yoy"]) and not np.isnan(early["dff_z"])
    late = money.loc[close.index[-1]]
    assert not np.isnan(late["walcl_yoy"])


# --------------------------------------------------------------------------- 점수
def test_score_regime_returns_similarity_result_shape():
    close, macro, cycle, events = _inputs()
    cfg = SimilarityConfig(window=WINDOW, top_k=5, mode="regime")
    res = score_regime(close.index[-1], close, macro, cycle, events, cfg)
    assert res.mode == "regime"
    assert res.layers_missing == []
    assert set(res.weights_used) == set(GROUPS)
    assert sum(res.weights_used.values()) == pytest.approx(1.0)
    assert res.weights_used == pytest.approx(REGIME_WEIGHTS)
    assert len(res.matches) == 5 and len(res.fit) == 5
    for m in res.matches:
        assert set(m.scores) == set(GROUPS)
        assert 0.0 <= m.combined <= 1.0
    comb = [m.combined for m in res.matches]
    assert comb == sorted(comb, reverse=True)
    pos = [close.index.get_loc(m.end_date) for m in res.matches]
    for i, p in enumerate(pos):
        assert len(close) - 1 - p >= cfg.min_gap_days
        assert all(abs(p - q) >= cfg.min_gap_days for q in pos[:i])
    assert list(res.forward_summary.index) == list(cfg.horizons)
    assert set(res.candidates.columns) == set(GROUPS) | {"combined"}


def test_same_environment_ranks_first():
    """환경(금리·VIX·업황·낙폭)이 지금과 똑같은 과거 시점을 심어 두면 1위로 찾는다.

    가격 모양은 다르게 둬서(모양 가중치 10%) 환경이 순위를 결정함을 확인한다.
    """
    rng = np.random.default_rng(42)
    n = 4000
    idx = pd.bdate_range("1994-01-03", periods=n)
    plant = 1500  # 심은 시점의 위치
    # 가격: 랜덤. 심은 시점과 끝 시점 모두 "고점 대비 -30%, 고점 이후 100일" 위치가 되도록 조작
    rets = rng.normal(0.0003, 0.015, n)
    close = pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)

    def plant_position(c: pd.Series, at: int) -> pd.Series:
        c = c.copy()
        peak = c.iloc[: at - 100].max() * 1.05  # 새 고점을 100일 전에 세운다
        seg = np.linspace(peak, peak * 0.70, 101)
        c.iloc[at - 100 : at + 1] = seg * (1 + rng.normal(0, 0.002, 101))
        c.iloc[at - 100] = peak
        c.iloc[at] = peak * 0.70
        return c

    close = plant_position(close, plant)
    close = plant_position(close, n - 1)
    # 환경 시리즈: 전 기간 랜덤, 심은 시점 앞뒤 200일과 끝 200일을 같은 수준으로 고정
    macro = {
        "DFF": pd.Series(np.clip(3 + np.cumsum(rng.normal(0, 0.02, n)), 0, 12), index=idx),
        "DGS10": pd.Series(np.clip(4 + np.cumsum(rng.normal(0, 0.02, n)), 0, 12), index=idx),
        "T10Y2Y": pd.Series(np.cumsum(rng.normal(0, 0.01, n)), index=idx),
        "VIXCLS": pd.Series(np.clip(20 + np.cumsum(rng.normal(0, 0.5, n)), 8, 80), index=idx),
    }
    fixed = {"DFF": 5.25, "DGS10": 4.5, "T10Y2Y": -0.5, "VIXCLS": 32.0}
    for k, v in fixed.items():
        macro[k].iloc[plant - 200 : plant + 1] = v
        macro[k].iloc[n - 200 :] = v
    months = pd.date_range("1993-01-31", periods=200, freq="ME")
    sales = 10_000 * np.exp(np.cumsum(rng.normal(0.004, 0.05, 200)))
    cycle = {"wsts_sales_world_usd_m": pd.Series(sales, index=months)}
    cfg = SimilarityConfig(window=WINDOW, top_k=5, mode="regime", cycle_lag_days=0)
    res = score_regime(idx[-1], close, macro, cycle, None, cfg)
    # 환경은 200일 동안 같게 심었으므로 "그 구간 안"을 1위로 찾으면 된다
    # (정확한 날짜는 랜덤인 업황·모양이 결정)
    top_pos = idx.get_loc(res.matches[0].end_date)
    assert plant - 200 <= top_pos <= plant
    assert res.matches[0].scores["position"] > 0.95
    assert res.matches[0].scores["money"] > 0.95
    # 심은 구간 밖의 2~5위는 환경 점수가 1위보다 낮다
    for m in res.matches[1:]:
        p = idx.get_loc(m.end_date)
        if not (plant - 200 <= p <= plant):
            assert m.scores["money"] < res.matches[0].scores["money"]
    # 심은 날짜 자체도 상위 후보에 든다 (업황·모양은 랜덤이라 정확히 1위는 아님; 상위 5% 안)
    cand = res.candidates["combined"]
    rank = int((cand > cand.loc[idx[plant]]).sum()) + 1
    assert rank <= len(cand) // 20


def test_no_lookahead_truncated_inputs_identical():
    close, macro, cycle, events = _inputs(seed=5)
    as_of = close.index[3200]
    cfg = SimilarityConfig(window=WINDOW, top_k=5, mode="regime")
    full = score_regime(as_of, close, macro, cycle, events, cfg)
    cut = score_regime(
        as_of,
        close.loc[:as_of],
        {k: v.loc[:as_of] for k, v in macro.items()},
        {k: v.loc[:as_of] for k, v in cycle.items()},
        events[events["date"] <= as_of],
        cfg,
    )
    assert full.n_candidates == cut.n_candidates
    assert [m.end_date for m in full.matches] == [m.end_date for m in cut.matches]
    for a, b in zip(full.matches, cut.matches, strict=True):
        assert a.combined == pytest.approx(b.combined, abs=1e-12)
        for g in GROUPS:
            assert a.scores[g] == pytest.approx(b.scores[g], abs=1e-12)
    assert [f.status for f in full.fit] == [f.status for f in cut.fit]
    assert full.applicable == cut.applicable


def test_missing_group_renormalizes_weights():
    close, _, cycle, _ = _inputs()
    cfg = SimilarityConfig(window=WINDOW, mode="regime")
    res = score_regime(close.index[-1], close, None, cycle, None, cfg)
    # 매크로가 없으면 money 그룹은 통째로 결측(모든 차원 NaN). policy는 금리 방향이 NaN이지만
    # 이벤트 차원(0)이 있어 살아 있다. narrative는 200일선 괴리가 있어 살아 있다.
    assert "money" in res.layers_missing
    assert "money" not in res.weights_used
    assert sum(res.weights_used.values()) == pytest.approx(1.0)
    assert res.applicable is None  # 금리가 없어 적합 기준 판정 불가


def test_score_mode_dispatch():
    close, macro, cycle, events = _inputs()
    r1 = score_mode(
        close.index[-1], close, macro, cycle, events, SimilarityConfig(window=WINDOW), "regime"
    )
    assert r1.mode == "regime"
    r2 = score_mode(
        close.index[-1], close, macro, cycle, events, SimilarityConfig(window=WINDOW), "shape"
    )
    assert r2.mode == "shape"
    # 기본값은 환경 우선 (2026-11-06 결정)
    r3 = score_mode(close.index[-1], close, macro, cycle, events, SimilarityConfig(window=WINDOW))
    assert r3.mode == "regime"
    # 모양 우선에는 환경용 시리즈(CPI·M2·WALCL)가 걸러져 들어간다 → 기본 macro_series만
    assert set(r2.weights_used) == {"price_shape", "macro", "semi_cycle"}
    with pytest.raises(ValueError):
        SimilarityConfig(mode="other")
