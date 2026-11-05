"""적합 기준(fit.py) 테스트. 계약: PHASE6_CONTRACT 23절. 네트워크·DB 없음."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import score
from stocker.similarity.fit import (
    FitFeatures,
    baseline_forward_summary,
    build_fit_features,
    check_fit,
    features_at,
    rate_direction,
    status_of,
)


def _now(**over) -> FitFeatures:
    base = dict(
        drawdown=-0.30, dd_days=100, rate_level=4.0, rate_dir="flat", wsts_yoy=20.0, vol=0.40
    )
    base.update(over)
    return FitFeatures(**base)


def _cond(fit, name):
    return next(c for c in fit.conditions if c.name == name)


# --------------------------------------------------------------------------- 조건별 경계값
def test_all_pass_is_strong():
    f = check_fit(_now(), _now())
    assert f.n_passed == 5 and f.status == "strong" and f.n_evaluable == 5
    assert [c.name for c in f.conditions] == ["drawdown", "dd_days", "rate", "cycle", "volatility"]


@pytest.mark.parametrize(
    "then_dd,passed",
    [(-0.221, True), (-0.379, True), (-0.219, False), (-0.381, False)],  # 8%p 경계 (now=-0.30)
)
def test_drawdown_boundary(then_dd, passed):
    f = check_fit(_now(drawdown=then_dd), _now())
    c = _cond(f, "drawdown")
    assert c.passed is passed
    assert c.then == pytest.approx(then_dd * 100) and c.now == pytest.approx(-30.0)
    assert c.unit == "%p" and c.tolerance == 8.0


@pytest.mark.parametrize(
    "then_days,passed",
    [(60, True), (166, True), (59, False), (167, False), (100, True)],  # 40% (긴 쪽 기준), now=100
)
def test_dd_days_boundary(then_days, passed):
    # |100-60|/100 = 0.40 통과, |166-100|/166 = 0.398 통과,
    # |59-100|/100 = 0.41 실패, |167-100|/167 = 0.401 실패
    f = check_fit(_now(dd_days=then_days), _now())
    assert _cond(f, "dd_days").passed is passed


def test_dd_days_both_zero_passes():
    f = check_fit(_now(dd_days=0), _now(dd_days=0))
    assert _cond(f, "dd_days").passed is True


@pytest.mark.parametrize(
    "then_rate,then_dir,passed",
    [
        (6.0, "flat", True),  # 2.0%p 경계 (now 4.0)
        (2.0, "flat", True),
        (6.01, "flat", False),
        (4.0, "up", False),  # 수준 같아도 방향 다르면 실패
        (5.0, "down", False),
    ],
)
def test_rate_boundary_level_and_direction(then_rate, then_dir, passed):
    f = check_fit(_now(rate_level=then_rate, rate_dir=then_dir), _now())
    c = _cond(f, "rate")
    assert c.passed is passed
    assert "방향" in (c.note or "")


@pytest.mark.parametrize(
    "then_yoy,passed", [(0.1, True), (144.0, True), (-0.1, False), (-30.0, False)]
)
def test_cycle_sign(then_yoy, passed):
    f = check_fit(_now(wsts_yoy=then_yoy), _now(wsts_yoy=20.0))
    assert _cond(f, "cycle").passed is passed


@pytest.mark.parametrize(
    "then_vol,passed",
    [(0.241, True), (0.6666, True), (0.239, False), (0.67, False)],  # 40% (큰 쪽 기준), now=0.40
)
def test_vol_boundary(then_vol, passed):
    f = check_fit(_now(vol=then_vol), _now())
    assert _cond(f, "volatility").passed is passed


# --------------------------------------------------------------------------- status
def test_status_thresholds():
    assert status_of(5) == "strong"
    assert status_of(4) == "ok" and status_of(3) == "ok"
    assert status_of(2) == "weak" and status_of(0) == "weak"


def test_status_from_counts():
    ok4 = check_fit(_now(rate_dir="up"), _now())
    assert ok4.n_passed == 4 and ok4.status == "ok"
    weak2 = check_fit(_now(rate_dir="up", drawdown=-0.1, dd_days=500), _now())
    assert weak2.n_passed == 2 and weak2.status == "weak"


def test_missing_data_is_not_passed():
    """자료가 없는 조건은 passed=None이고 통과로 치지 않는다(보수적)."""
    f = check_fit(_now(rate_level=None, wsts_yoy=None), _now())
    assert _cond(f, "rate").passed is None and _cond(f, "cycle").passed is None
    assert f.n_passed == 3 and f.n_evaluable == 3 and f.status == "ok"
    # dict 입력도 받는다
    g = check_fit({"drawdown": -0.3, "dd_days": 100}, _now())
    assert g.n_passed == 2 and g.status == "weak"


def test_to_dict_is_jsonable():
    d = check_fit(_now(), _now()).to_dict()
    assert d["status"] == "strong" and len(d["conditions"]) == 5
    assert set(d["conditions"][0]) >= {"name", "then", "now", "tolerance", "passed"}


# --------------------------------------------------------------------------- 특징 계산
def test_rate_direction_threshold():
    chg = pd.Series([0.3, 0.25, 0.0, -0.25, -0.3, np.nan])
    out = rate_direction(chg, 0.25)
    assert list(out[:5]) == ["up", "flat", "flat", "flat", "down"]
    assert out.iloc[5] is None


def test_build_fit_features_values():
    idx = pd.bdate_range("2000-01-03", periods=400)
    close = pd.Series(np.linspace(100, 200, 400), index=idx)  # 계속 신고가
    close.iloc[-50:] = 150.0  # 마지막 50일은 고점(200 근처) 아래 평평
    dff = pd.Series(np.r_[np.full(300, 1.0), np.full(100, 2.0)], index=idx)  # 뒤에서 +1%p
    months = pd.date_range("1999-01-31", periods=36, freq="ME")  # 1999-01 ~ 2001-12
    wsts = pd.Series(np.r_[np.full(24, 100.0), np.full(12, 80.0)], index=months)  # 2001년 YoY -20%
    cfg = SimilarityConfig(cycle_lag_days=0, change_days=126)
    f = build_fit_features(close, {"DFF": dff}, {"wsts_sales_world_usd_m": wsts}, cfg)
    last = features_at(f, idx[-1])
    assert last.drawdown == pytest.approx(150.0 / close.iloc[-51] - 1.0)
    assert last.dd_days == 50
    assert last.rate_level == 2.0 and last.rate_dir == "up"
    assert last.wsts_yoy == pytest.approx(-20.0)
    assert last.vol is not None and last.vol > 0  # 60일 창 안에 하락(200→150) 하루가 들어 있다
    early = features_at(f, idx[200])
    assert early.dd_days == 0 and early.rate_dir == "flat"
    assert features_at(f, pd.Timestamp("1990-01-01")).drawdown is None


def test_fit_features_no_lookahead():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2000-01-03", periods=800)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 800))), index=idx)
    dff = pd.Series(np.cumsum(rng.normal(0, 0.02, 800)) + 3, index=idx)
    cut = idx[500]
    full = build_fit_features(close, {"DFF": dff}, None)
    part = build_fit_features(close.loc[:cut], {"DFF": dff.loc[:cut]}, None)
    pd.testing.assert_frame_equal(full.loc[:cut], part)


# --------------------------------------------------------------------------- 엔진 통합 / baseline
def _synthetic_inputs(seed=0, n=3000):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("1994-01-03", periods=n)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n))), index=idx)
    macro = {"DFF": pd.Series(np.clip(np.cumsum(rng.normal(0, 0.02, n)) + 3, 0, None), index=idx)}
    months = pd.date_range("1993-01-31", periods=160, freq="ME")
    cycle = {
        "wsts_sales_world_usd_m": pd.Series(
            10_000 * np.exp(np.cumsum(rng.normal(0.004, 0.04, 160))), index=months
        )
    }
    return close, macro, cycle


def test_baseline_summary_uses_only_known_forward():
    close, _, _ = _synthetic_inputs()
    hist = close.iloc[:1000]
    bs = baseline_forward_summary(hist, (21, 252))
    assert bs.loc[21, "n"] == 1000 - 21 and bs.loc[252, "n"] == 1000 - 252
    # as_of 이후 데이터를 붙여도 hist만 쓰므로 값은 변하지 않는다 (호출자가 hist를 잘라 넘김)
    exp = (hist.shift(-21) / hist - 1).dropna()
    assert bs.loc[21, "median"] == pytest.approx(float(np.median(exp)))


def test_score_reports_fit_and_applicable():
    close, macro, cycle = _synthetic_inputs()
    cfg = SimilarityConfig(window=60, top_k=5)
    res = score(close.index[-1], close, macro, cycle, cfg)
    assert len(res.fit) == len(res.matches)
    assert all(f.status in ("strong", "ok", "weak") for f in res.fit)
    assert res.applicable in (True, False)
    n_ok = sum(1 for f in res.fit if f.status != "weak")
    assert res.applicable_n == n_ok
    if n_ok == 0:
        assert res.forward_summary_source == "baseline"
        assert res.forward_summary.loc[21, "n"] > cfg.top_k
    else:
        assert res.forward_summary_source == "matches"
        assert res.forward_summary.loc[21, "n"] == n_ok
        assert res.applicable == (n_ok >= cfg.fit_min_applicable)


def test_all_weak_falls_back_to_baseline():
    """과거가 전혀 다른 환경이 되도록 만들면(금리·업황·변동성 모두 다름) 기준선으로 대체된다."""
    close, macro, cycle = _synthetic_inputs(seed=3)
    # 기준일 쪽 마지막 400일만 변동성을 5배로, 금리를 +15%p로, 매출을 급감시켜 모든 과거와 다르게
    rng = np.random.default_rng(9)
    tail = 100 * np.exp(np.cumsum(rng.normal(0, 0.10, 400)))
    close2 = close.copy()
    close2.iloc[-400:] = tail * (close.iloc[-401] / tail[0])
    macro2 = {"DFF": macro["DFF"].copy()}
    macro2["DFF"].iloc[-400:] += 15.0
    cfg = SimilarityConfig(window=60, top_k=5)
    res = score(close2.index[-1], close2, macro2, cycle, cfg)
    assert all(f.status == "weak" for f in res.fit)
    assert res.applicable is False and res.applicable_n == 0
    assert res.forward_summary_source == "baseline"
    hist = close2.loc[: res.as_of]
    pd.testing.assert_frame_equal(res.forward_summary, baseline_forward_summary(hist, cfg.horizons))


def test_fit_not_evaluable_without_macro_keeps_old_behaviour():
    close, _, cycle = _synthetic_inputs()
    cfg = SimilarityConfig(window=60, top_k=5)
    res = score(close.index[-1], close, None, None, cfg)
    assert res.applicable is None
    assert res.forward_summary_source == "matches"
    assert res.forward_summary.loc[21, "n"] == len(res.matches)
    assert len(res.fit) == len(res.matches)
    for f in res.fit:
        assert _cond(f, "rate").passed is None and _cond(f, "cycle").passed is None


def test_score_always_carries_baseline_and_it_has_no_lookahead():
    """forward_summary_baseline: 항상 채워지고 as_of 이후 데이터는 쓰지 않는다."""
    close, macro, cycle = _synthetic_inputs()
    cfg = SimilarityConfig(window=60, top_k=5)
    cut = close.index[1500]
    full = score(cut, close, macro, cycle, cfg)  # as_of 이후 데이터가 입력에 있어도
    trimmed = score(cut, close.loc[:cut], macro, cycle, cfg)  # 잘라 넣은 것과 같아야 한다
    assert full.forward_summary_baseline is not None
    pd.testing.assert_frame_equal(full.forward_summary_baseline, trimmed.forward_summary_baseline)
    expected = baseline_forward_summary(close.loc[:cut], cfg.horizons)
    pd.testing.assert_frame_equal(full.forward_summary_baseline, expected)
    # as_of 이후 구간은 n에 들어가지 않는다 (끝 h행은 결과를 아직 몰랐던 창)
    n_hist = int((close.index <= cut).sum())
    assert full.forward_summary_baseline.loc[252, "n"] == n_hist - 252
    # 닮은 국면 분포가 기준선과 같은 객체가 아니어도 되고, 둘 다 같은 열을 가진다
    assert list(full.forward_summary_baseline.columns) == list(full.forward_summary.columns)
