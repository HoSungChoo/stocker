"""백테스트(stocker.backtest) 테스트. 계약: PHASE6_CONTRACT 25절. 기본 실행은 네트워크·DB 없음."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocker.backtest.harness import (
    RUN_COLUMNS,
    evaluate_point,
    month_end_dates,
    run_backtest_from_inputs,
)
from stocker.backtest.metrics import (
    ERAS,
    aggregate,
    direction_series,
    era_of,
    hit_series,
    interval_score,
    interval_score_series,
    verdict,
)
from stocker.backtest.report import build_report
from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import _clean_close
from stocker.similarity.fit import baseline_forward_summary

WINDOW = 60


def _inputs(seed: int = 0, n: int = 5000):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("1994-01-03", periods=n)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n))), index=idx)
    macro = {
        "DFF": pd.Series(np.clip(3 + np.cumsum(rng.normal(0, 0.01, n)), 0, None), index=idx),
        "VIXCLS": pd.Series(np.clip(20 + np.cumsum(rng.normal(0, 0.3, n)), 5, None), index=idx),
        "DGS10": pd.Series(np.clip(4 + np.cumsum(rng.normal(0, 0.01, n)), 0, None), index=idx),
    }
    months = pd.date_range("1993-01-31", periods=250, freq="ME")
    cycle = {
        "wsts_sales_world_usd_m": pd.Series(
            10_000 * np.exp(np.cumsum(rng.normal(0.004, 0.04, 250))), index=months
        )
    }
    events = pd.DataFrame({"date": pd.to_datetime(["2001-09-11"]), "kind": ["war"]})
    return close, macro, cycle, events


# --------------------------------------------------------------------------- 구간 점수 손계산
def test_interval_score_hand_computed():
    # 폭 0.3, 실제값이 안 → 0.3
    assert interval_score(-0.1, 0.2, 0.05) == pytest.approx(0.3)
    # 실제값이 아래로 0.1 벗어남 → 0.3 + 10*0.1 = 1.3  (α=0.2 → 2/α = 10)
    assert interval_score(-0.1, 0.2, -0.2) == pytest.approx(1.3)
    # 위로 0.05 벗어남 → 0.3 + 0.5 = 0.8
    assert interval_score(-0.1, 0.2, 0.25) == pytest.approx(0.8)
    # 경계값은 안으로 본다
    assert interval_score(-0.1, 0.2, 0.2) == pytest.approx(0.3)
    # 한 점 분포(p10=p90): 폭 0 + 벌점만
    assert interval_score(0.5, 0.5, 0.6) == pytest.approx(1.0)
    assert np.isnan(interval_score(np.nan, 0.2, 0.1))
    # α를 바꾸면 벌점 배율이 바뀐다
    assert interval_score(-0.1, 0.2, -0.2, alpha=0.5) == pytest.approx(0.3 + 4 * 0.1)


def test_vector_metrics_match_scalar():
    df = pd.DataFrame(
        {
            "median": [0.1, -0.1, 0.0, 0.2],
            "p10": [-0.1, -0.3, -0.2, 0.0],
            "p90": [0.2, 0.1, 0.2, 0.4],
            "realized": [0.05, -0.2, 0.25, np.nan],
        }
    )
    got = interval_score_series(df).to_numpy()
    exp = [interval_score(r.p10, r.p90, r.realized) for r in df.itertuples()]
    np.testing.assert_allclose(got[:3], exp[:3])
    assert np.isnan(got[3])
    np.testing.assert_array_equal(hit_series(df).to_numpy()[:3], [1.0, 1.0, 0.0])
    assert np.isnan(hit_series(df).iloc[3])
    np.testing.assert_array_equal(direction_series(df).to_numpy()[:3], [1.0, 1.0, 0.0])


def test_era_of():
    assert era_of("1999-01-29") == "1999–2006" and era_of("2006-12-29") == "1999–2006"
    assert era_of("2007-01-31") == "2007–2012" and era_of("2013-06-28") == "2013–2019"
    assert era_of("2025-10-31") == "2020–2025" and era_of("1998-12-31") is None
    assert list(ERAS) == ["1999–2006", "2007–2012", "2013–2019", "2020–2025"]


# --------------------------------------------------------------------------- 판정 (승리 조건)
def _runs_from_scores(scores: dict[str, dict[str, list[float]]]) -> pd.DataFrame:
    """mode → era → [구간 점수...]를 backtest_runs 형식으로 (폭 = 점수, 실제값은 안에)."""
    era_dates = {
        "1999–2006": "2000-06-30",
        "2007–2012": "2008-06-30",
        "2013–2019": "2015-06-30",
        "2020–2025": "2021-06-30",
    }
    rows = []
    for mode, by_era in scores.items():
        for era, widths in by_era.items():
            for i, w in enumerate(widths):
                d = pd.Timestamp(era_dates[era]) + pd.Timedelta(days=i)
                rows.append(
                    dict(
                        mode=mode,
                        as_of=d,
                        horizon=252,
                        median=0.1,
                        p10=0.0,
                        p90=w,
                        n=5,
                        applicable=None if mode == "baseline" else True,
                        realized=w / 2,
                    )
                )
    return pd.DataFrame(rows)


def test_verdict_rules():
    base = {e: [1.0, 1.0] for e in ERAS}
    # regime: 전체·시대 4개 모두 낮음 → 승. shape: 전체는 낮지만 시대 2개만 → 패
    runs = _runs_from_scores(
        {
            "baseline": base,
            "regime": {e: [0.8, 0.8] for e in ERAS},
            "shape": {
                "1999–2006": [0.5, 0.5],
                "2007–2012": [0.5, 0.5],
                "2013–2019": [1.2, 1.2],
                "2020–2025": [1.1, 1.1],
            },
        }
    )
    agg = aggregate(runs, 252)
    v = verdict(agg)
    assert v.beats_baseline == {"shape": False, "regime": True}
    assert v.eras_won["regime"] == list(ERAS)
    assert v.eras_won["shape"] == ["1999–2006", "2007–2012"]
    assert v.winner == "regime"

    # 둘 다 못 이김 → winner None, 결론에 "좁히지 못한다"
    runs2 = _runs_from_scores(
        {
            "baseline": base,
            "regime": {e: [1.1, 1.1] for e in ERAS},
            "shape": {e: [1.2] for e in ERAS},
        }
    )
    v2 = verdict(aggregate(runs2, 252))
    assert v2.winner is None and "좁히지 못한다" in v2.conclusion

    # 전체는 낮지만 시대 3개 미만 → 패 (시대 조건)
    runs3 = _runs_from_scores(
        {
            "baseline": base,
            "regime": {
                "1999–2006": [0.1, 0.1],
                "2007–2012": [1.2, 1.2],
                "2013–2019": [1.2, 1.2],
                "2020–2025": [0.9, 0.9],
            },
        }
    )
    v3 = verdict(aggregate(runs3, 252))
    assert v3.overall_is["regime"] < v3.overall_is["baseline"]
    assert v3.beats_baseline["regime"] is False

    with pytest.raises(ValueError):
        verdict(aggregate(_runs_from_scores({"shape": base}), 252))


# --------------------------------------------------------------------------- 시점·실행
def test_month_end_dates():
    idx = pd.bdate_range("1998-11-02", "1999-04-30")
    out = month_end_dates(idx, "1999-01", "1999-03")
    assert [d.strftime("%Y-%m-%d") for d in out] == ["1999-01-29", "1999-02-26", "1999-03-31"]
    assert month_end_dates(idx, "2010-01", "2010-02") == []


def test_shape_env_is_shape_engine_with_env_weights():
    close, macro, cycle, events = _inputs(seed=4)
    full = _clean_close(close)
    as_of = full.index[2500]
    cfg = SimilarityConfig(window=WINDOW, top_k=5)
    rows = evaluate_point("shape_env", as_of, full, macro, cycle, events, cfg, (252,))
    assert rows[0]["mode"] == "shape_env"
    # 같은 가중치를 직접 넣은 모양 우선 결과와 동일하다
    from stocker.similarity.engine import score

    cfg_env = SimilarityConfig(
        window=WINDOW, top_k=5, weights={"price_shape": 0.1, "macro": 0.6, "semi_cycle": 0.3}
    )
    res = score(as_of, full, macro, cycle, cfg_env)
    assert rows[0]["median"] == pytest.approx(res.forward_summary.loc[252, "median"], nan_ok=True)
    assert rows[0]["n"] == res.forward_summary.loc[252, "n"]
    assert rows[0]["applicable"] == res.applicable
    with pytest.raises(ValueError):
        evaluate_point("nope", as_of, full, macro, cycle, events, cfg, (252,))


def test_reference_mode_cannot_win():
    base = {e: [1.0, 1.0] for e in ERAS}
    runs = _runs_from_scores(
        {
            "baseline": base,
            "shape": {e: [1.3] for e in ERAS},
            "shape_env": {e: [0.5] for e in ERAS},  # 기준선을 이기지만 참고 줄
            "regime": {e: [1.2] for e in ERAS},
        }
    )
    v = verdict(aggregate(runs, 252))
    assert v.beats_baseline["shape_env"] is True
    assert v.winner is None and "좁히지 못한다" in v.conclusion
    md = build_report(runs, 252, generated_at="test")
    assert "모양 우선·환경 가중(참고)" in md and "참고 줄" in md


def test_baseline_point_matches_pure_function_and_realized():
    close, macro, cycle, events = _inputs()
    full = _clean_close(close)
    as_of = full.index[2000]
    cfg = SimilarityConfig(window=WINDOW)
    rows = evaluate_point("baseline", as_of, full, macro, cycle, events, cfg, (21, 252))
    assert [r["horizon"] for r in rows] == [21, 252]
    bs = baseline_forward_summary(full.loc[:as_of], (21, 252))
    assert rows[1]["median"] == pytest.approx(bs.loc[252, "median"])
    assert rows[1]["n"] == bs.loc[252, "n"] == 2001 - 252
    assert rows[1]["applicable"] is None
    assert rows[1]["realized"] == pytest.approx(full.iloc[2252] / full.iloc[2000] - 1)
    # 끝에서는 realized가 NaN
    tail = evaluate_point("baseline", full.index[-1], full, macro, cycle, events, cfg, (252,))
    assert np.isnan(tail[0]["realized"])


def test_run_backtest_no_lookahead():
    """임의 as_of에서 그 이후 데이터를 잘라도 분포(median/p10/p90/n/applicable)가 같다."""
    close, macro, cycle, events = _inputs(seed=2)
    cfg = SimilarityConfig(window=WINDOW, top_k=5)
    dates = month_end_dates(close.index, "2005-01", "2005-03")
    cut = dates[-1]
    full = run_backtest_from_inputs(
        dates, close, macro, cycle, events, ("baseline", "shape", "regime"), cfg
    )
    part = run_backtest_from_inputs(
        dates,
        close.loc[:cut],
        {k: v.loc[:cut] for k, v in macro.items()},
        {k: v.loc[:cut] for k, v in cycle.items()},
        events[events["date"] <= cut],
        ("baseline", "shape", "regime"),
        cfg,
    )
    cols = ["mode", "as_of", "horizon", "median", "p10", "p90", "n", "applicable"]
    pd.testing.assert_frame_equal(
        full[cols].reset_index(drop=True), part[cols].reset_index(drop=True)
    )
    # realized만 다르다(잘린 쪽은 미래가 없어 NaN)
    assert full["realized"].notna().all()
    assert part[part["horizon"] == 252]["realized"].isna().all()


def test_end_to_end_small():
    close, macro, cycle, events = _inputs(seed=7)
    cfg = SimilarityConfig(window=WINDOW, top_k=5)
    dates = month_end_dates(close.index, "2003-01", "2003-10")
    assert len(dates) == 10
    runs = run_backtest_from_inputs(
        dates, close, macro, cycle, events, ("baseline", "shape", "regime"), cfg
    )
    assert list(runs.columns) == RUN_COLUMNS
    assert len(runs) == 10 * 3 * len(cfg.horizons)
    assert set(runs["mode"]) == {"baseline", "shape", "regime"}
    base = runs[runs["mode"] == "baseline"]
    assert base["applicable"].isna().all() and (base["n"] > 100).all()
    matched = runs[runs["mode"] != "baseline"]
    assert matched["applicable"].isin([True, False]).all()  # 금리·업황이 있어 판정 가능
    # applicable=False이고 n이 큰 행은 기준선과 같은 분포여야 한다 (기준선 대체 규칙)
    for _, r in matched[(matched["applicable"] == False) & (matched["n"] > cfg.top_k)].iterrows():  # noqa: E712
        b = base[(base["as_of"] == r["as_of"]) & (base["horizon"] == r["horizon"])].iloc[0]
        assert r["median"] == pytest.approx(b["median"]) and r["n"] == b["n"]
    agg = aggregate(runs, 252)
    assert set(agg["era"]) == {"전체", *ERAS}
    total = agg[(agg["era"] == "전체")].set_index("mode")
    assert total.loc["baseline", "n"] == 10
    assert np.isnan(total.loc["baseline", "not_applicable_rate"])
    assert 0.0 <= total.loc["shape", "not_applicable_rate"] <= 1.0
    v = verdict(agg)
    assert v.winner in (None, "shape", "regime")
    md = build_report(runs, 252, generated_at="test")
    assert "# 백테스트 결과" in md and "## 판정" in md and "## 쉬운 설명" in md
    assert "시대 1999–2006" in md and v.conclusion in md
    assert "| 기준선(baseline) | 10 |" in md


# --------------------------------------------------------------------------- DB
@pytest.mark.db
def test_backtest_runs_round_trip(engine):
    import sqlalchemy as sa

    from stocker.db import init_db, read_backtest_runs, write_backtest_runs

    init_db(engine)
    df = pd.DataFrame(
        {
            "mode": ["test_mode", "test_mode"],
            "as_of": pd.to_datetime(["2001-01-31", "2001-02-28"]),
            "horizon": [252, 252],
            "median": [0.1, np.nan],
            "p10": [-0.2, -0.3],
            "p90": [0.5, 0.6],
            "n": [5, 3000],
            "applicable": [True, None],
            "realized": [0.2, np.nan],
        }
    )
    try:
        assert write_backtest_runs(df, engine) == 2
        out = read_backtest_runs(modes=["test_mode"], horizon=252, engine=engine)
        assert len(out) == 2 and list(out["as_of"].dt.strftime("%Y-%m-%d")) == [
            "2001-01-31",
            "2001-02-28",
        ]
        assert out["applicable"].tolist()[0] is True and out["applicable"].isna().tolist()[1]
        assert np.isnan(out["median"].iloc[1]) and out["n"].tolist() == [5, 3000]
        # 같은 키로 다시 쓰면 덮어쓴다
        df2 = df.copy()
        df2["median"] = [0.9, 0.9]
        write_backtest_runs(df2, engine)
        out2 = read_backtest_runs(modes=["test_mode"], engine=engine)
        assert len(out2) == 2 and out2["median"].tolist() == [0.9, 0.9]
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM backtest_runs WHERE mode = 'test_mode'"))


# ------------------------------------------------------------------ Phase 6B (PLAN_PHASE6B)
def test_phase6b_preset_arms_match_plan_table():
    """2절 표: arm 9개, (모드, top_k, min_gap)이 문서대로, 순서도 문서대로."""
    from stocker.backtest.presets import PHASE6B_ARMS, get_preset

    arms = get_preset("phase6b")
    assert arms is PHASE6B_ARMS and len(arms) == 9
    assert [(a.name, a.mode, a.top_k, a.min_gap) for a in arms] == [
        ("baseline", "baseline", None, None),
        ("shape_k5", "shape", 5, 250),
        ("regime_k5", "regime", 5, 250),
        ("shape_k10", "shape", 10, 250),
        ("regime_k10", "regime", 10, 250),
        ("shape_k20", "shape", 20, 126),
        ("regime_k20", "regime", 20, 126),
        ("shape_k30", "shape", 30, 63),
        ("regime_k30", "regime", 30, 63),
    ]
    # k=5 두 개만 참고 줄(Phase 6과 동일 설정)
    assert [a.name for a in arms if a.reference] == ["shape_k5", "regime_k5"]
    with pytest.raises(ValueError):
        get_preset("nope")


def test_phase6b_arm_config_changes_only_k_and_gap():
    from stocker.backtest.harness import arm_config
    from stocker.backtest.presets import arm_by_name, get_preset

    cfg = SimilarityConfig(window=WINDOW)
    arm = arm_by_name(get_preset("phase6b"), "regime_k20")
    c2 = arm_config(arm, cfg)
    assert (c2.top_k, c2.min_gap_days, c2.mode) == (20, 126, "regime")
    # 나머지(가중치·적합 기준·창 길이)는 그대로
    assert c2.window == WINDOW and c2.weights == cfg.weights
    assert c2.regime_weights == cfg.regime_weights and c2.fit_drawdown_pp == cfg.fit_drawdown_pp
    assert arm_config(arm_by_name(get_preset("phase6b"), "baseline"), cfg) is cfg


def test_effective_count_hand_computed():
    """실질 표본 수: 서로 gap 이상 떨어진 위치의 수 (정렬 후 탐욕)."""
    from stocker.backtest.presets import effective_count

    # 0, 50, 200, 330 / gap 126 → 0 ✓, 50 ✗(0과 50), 200 ✓(0과 200), 330 ✓(200과 130) → 3
    assert effective_count([0, 50, 200, 330], 126) == 3
    # 순서가 섞여도 같다
    assert effective_count([330, 0, 200, 50], 126) == 3
    # 모두 촘촘하면 1, 비어 있으면 0, 하나면 1
    assert effective_count([10, 20, 30, 40], 126) == 1
    assert effective_count([], 126) == 0
    assert effective_count([7], 126) == 1
    # 경계: 정확히 gap만큼 떨어지면 서로 다른 표본으로 센다
    assert effective_count([0, 126, 252], 126) == 3
    assert effective_count([0, 125, 250], 126) == 2  # 0 ✓, 125 ✗, 250 ✓(0과 250)
    # min_gap 250으로 뽑힌 창들은 gap 126 기준으로 전부 독립
    assert effective_count(range(0, 2500, 250), 126) == 10


def test_effective_sample_size_from_result():
    """엔진 결과에서 실질 표본 수: ok 이상인 창만 세고, 기준선 대체면 NaN."""
    from stocker.backtest.harness import distribution_dates, effective_sample_size
    from stocker.similarity.fit import is_ok
    from stocker.similarity.loaders import score_mode

    close, macro, cycle, events = _inputs(seed=11)
    full = _clean_close(close)
    as_of = full.index[4500]
    cfg = SimilarityConfig(window=WINDOW, top_k=30, min_gap_days=63)
    res = score_mode(as_of, full, macro, cycle, events, cfg, "shape")
    dates = distribution_dates(res)
    if res.forward_summary_source == "baseline":
        assert len(dates) == 0 and np.isnan(effective_sample_size(res, full))
        return
    ok = [m.end_date for m, f in zip(res.matches, res.fit, strict=True) if is_ok(f)]
    assert list(dates) == ok and len(ok) == int(res.forward_summary.loc[252, "n"])
    pos = np.sort(full.index.get_indexer(dates))
    # 손계산과 같고, 범위는 1 ≤ n_eff ≤ n
    expected, last = 1, int(pos[0])
    for p in pos[1:]:
        if int(p) - last >= 126:
            expected, last = expected + 1, int(p)
    assert effective_sample_size(res, full) == expected
    assert 1 <= expected <= len(ok)


def test_phase6b_rows_and_no_lookahead_single_point():
    """한 시점: arm 행에 preset·top_k·min_gap·n_eff가 붙고, 그 이후 데이터를 잘라도 분포가 같다."""
    from stocker.backtest.harness import PRESET_COLUMNS, evaluate_arm
    from stocker.backtest.presets import arm_by_name, get_preset

    close, macro, cycle, events = _inputs(seed=3)
    full = _clean_close(close)
    as_of = full.index[4200]
    cfg = SimilarityConfig(window=WINDOW)
    arms = get_preset("phase6b")
    for name in ("shape_k10", "regime_k20", "shape_k30"):
        arm = arm_by_name(arms, name)
        rows = evaluate_arm(arm, as_of, full, macro, cycle, events, cfg, (252,), "phase6b")
        r = rows[0]
        assert r["mode"] == name and r["preset"] == "phase6b"
        assert (r["top_k"], r["min_gap"]) == (arm.top_k, arm.min_gap)
        assert set(PRESET_COLUMNS) <= set(r)
        # 분포를 만든 국면 수는 top_k를 넘지 않고, n_eff ≤ n (기준선 대체가 아니면)
        if r["n"] <= arm.top_k:
            assert r["n_eff"] <= r["n"]
        # 룩어헤드: as_of 이후를 잘라도 같은 분포
        cut_rows = evaluate_arm(
            arm,
            as_of,
            full.loc[:as_of],
            {k: v.loc[:as_of] for k, v in macro.items()},
            {k: v.loc[:as_of] for k, v in cycle.items()},
            events[events["date"] <= as_of],
            cfg,
            (252,),
            "phase6b",
        )
        c = cut_rows[0]
        for col in ("median", "p10", "p90"):
            assert r[col] == pytest.approx(c[col], nan_ok=True)
        assert (r["n"], r["applicable"]) == (c["n"], c["applicable"])
        assert r["n_eff"] == pytest.approx(c["n_eff"], nan_ok=True)
        assert np.isnan(c["realized"]) and not np.isnan(r["realized"])


def test_phase6b_preset_run_and_report_small():
    from stocker.backtest.harness import PRESET_COLUMNS, run_preset_from_inputs
    from stocker.backtest.phase6b import build_report_k, interpret
    from stocker.backtest.presets import get_preset

    close, macro, cycle, events = _inputs(seed=5)
    cfg = SimilarityConfig(window=WINDOW)
    dates = month_end_dates(close.index, "2008-01", "2008-06")
    runs = run_preset_from_inputs(
        get_preset("phase6b"), dates, close, macro, cycle, events, cfg, (252,), preset="phase6b"
    )
    assert list(runs.columns) == RUN_COLUMNS + PRESET_COLUMNS
    assert len(runs) == 6 * 9 and set(runs["preset"]) == {"phase6b"}
    assert runs[runs["mode"] == "baseline"]["n_eff"].isna().all()
    arms = get_preset("phase6b")
    agg = aggregate(
        runs, 252, top_k={a.name: a.top_k for a in arms if a.top_k}, modes=[a.name for a in arms]
    )
    assert list(agg[agg["era"] == "전체"]["mode"]) == [a.name for a in arms]
    interp = interpret(agg, arms)
    assert [r.arm.name for r in interp.rows] == [a.name for a in arms]
    md = build_report_k(runs, arms, 252, generated_at="test")
    assert "# 백테스트 결과 (Phase 6B)" in md and interp.conclusion in md
    assert "| shape_k10 | 모양 우선 | 10 | 250 |" in md and "평균 실질 표본 수" in md


def _runs_for_arms(scores: dict[str, float], hit: float = 0.8) -> pd.DataFrame:
    """arm 이름 → 전체·모든 시대에 같은 구간 점수(폭)인 가짜 결과. realized는 폭 안."""
    era_dates = ["2000-06-30", "2008-06-30", "2015-06-30", "2021-06-30"]
    rows = []
    for mode, w in scores.items():
        for d0 in era_dates:
            for i in range(5):
                rows.append(
                    dict(
                        mode=mode,
                        as_of=pd.Timestamp(d0) + pd.Timedelta(days=i),
                        horizon=252,
                        median=0.1,
                        p10=0.0,
                        p90=w,
                        n=5,
                        applicable=None if mode == "baseline" else True,
                        realized=w / 2 if i < 5 * hit else w * 2,
                        n_eff=4.0 if mode != "baseline" else np.nan,
                    )
                )
    return pd.DataFrame(rows)


def test_phase6b_interpretation_rules():
    """4절 해석 규칙의 기계 적용."""
    from stocker.backtest.phase6b import interpret
    from stocker.backtest.presets import get_preset

    arms = get_preset("phase6b")
    names = [a.name for a in arms]
    top_k = {a.name: a.top_k for a in arms if a.top_k}

    def run(scores):
        agg = aggregate(_runs_for_arms(scores), 252, top_k=top_k, modes=names)
        return interpret(agg, arms)

    base = {n: 1.5 for n in names}  # 전부 기준선(1.0)보다 나쁨
    base["baseline"] = 1.0
    # 1) 어느 k에서도 못 이김 → 확정 결론
    v = run(base)
    assert v.winners == [] and "좁히지 못한다" in v.conclusion and "사례" in v.conclusion
    # 2) k=30에서만 이김 → "미약"
    v = run({**base, "shape_k30": 0.8, "regime_k30": 0.7})
    assert v.winners == ["shape_k30", "regime_k30"] and v.core_winners == []
    assert "미약" in v.conclusion
    # 3) 환경 우선만 k=10·20에서 이김 → 환경 매칭 효과, 모양 없음
    v = run({**base, "regime_k10": 0.9, "regime_k20": 0.8})
    assert v.core_winners == ["regime_k10", "regime_k20"] and v.winning_modes == ["regime"]
    assert "환경 매칭은 효과가 있다, 모양 매칭은 없다" in v.conclusion
    # 4) 참고 줄(k=5)이 이겨도 규칙 적용 대상이 아니다
    v = run({**base, "shape_k5": 0.5})
    assert v.winners == [] and "좁히지 못한다" in v.conclusion
    # 5) 전체는 낮지만 시대 조건을 못 채우면 승리가 아니다 (compare_to_baseline 규칙)
    from stocker.backtest.metrics import compare_to_baseline

    runs = _runs_for_arms({**base, "regime_k10": 1.05})
    sub = runs["mode"] == "regime_k10"
    runs.loc[sub & (runs["as_of"].dt.year == 2000), "p90"] = 0.01  # 한 시대만 크게 좋음
    runs.loc[sub & (runs["as_of"].dt.year == 2000), "realized"] = 0.005
    agg = aggregate(runs, 252, top_k=top_k, modes=names)
    c = compare_to_baseline(agg, names)["regime_k10"]
    assert c.overall_is < c.baseline_is and c.eras_won == ["1999–2006"] and not c.beats_baseline


def test_phase6b_hit_range_flag():
    from stocker.backtest.phase6b import _in_range
    from stocker.backtest.presets import HIT_RANGE

    assert HIT_RANGE == (0.60, 0.95)
    assert _in_range(0.60) is True and _in_range(0.95) is True and _in_range(0.8) is True
    assert _in_range(0.33) is False and _in_range(0.96) is False and _in_range(float("nan")) is None
