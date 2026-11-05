"""stocker.sim.leveraged 테스트. 기본 실행은 네트워크 없음."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocker.sim import calibrate, forward_returns, simulate_leveraged
from stocker.sim.leveraged import align_financing_rate


def _bdays(n: int, start: str = "2020-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def test_constant_return_zero_cost():
    r, n, lev = 0.001, 100, 3.0
    rets = pd.Series(r, index=_bdays(n))
    nav = simulate_leveraged(rets, leverage=lev, expense_ratio=0.0, start_value=1.0)
    assert nav.iloc[-1] == pytest.approx((1 + lev * r) ** n, rel=1e-12)
    assert list(nav.index) == list(rets.index)


def test_volatility_decay_alternating():
    n = 252
    rets = pd.Series([0.10, -0.10] * (n // 2), index=_bdays(n))
    idx_nav = (1 + rets).cumprod().iloc[-1]
    lev_nav = simulate_leveraged(rets, leverage=3.0, expense_ratio=0.0, start_value=1.0)
    # 지수 자체도 감가 (0.99^126), 3배는 그보다 훨씬 심하다 (0.91^126)
    assert idx_nav < 1.0
    assert lev_nav.iloc[-1] < idx_nav**3 < 1.0
    assert lev_nav.iloc[-1] == pytest.approx(0.91 ** (n // 2), rel=1e-10)


def test_expense_ratio_only():
    er = 0.0075
    rets = pd.Series(0.0, index=_bdays(252))
    nav = simulate_leveraged(rets, leverage=3.0, expense_ratio=er, start_value=100.0)
    assert nav.iloc[-1] == pytest.approx(100 * np.exp(-er), rel=1e-4)
    assert nav.iloc[-1] == pytest.approx(100 * (1 - er / 252) ** 252, rel=1e-12)


def test_financing_series_alignment_ffill():
    idx = _bdays(5, "2024-01-01")  # Mon 01-01 .. Fri 01-05
    # 퍼센트 단위(DFF 형식). 시작 전 관측, 중간 관측, 주말 관측이 섞임.
    rf = pd.Series(
        [5.0, 2.0, 9.0],
        index=pd.to_datetime(["2023-12-29", "2024-01-03", "2024-01-06"]),
    )
    aligned = align_financing_rate(rf, idx, rate_scale="percent")
    np.testing.assert_allclose(aligned.to_numpy(), [0.05, 0.05, 0.02, 0.02, 0.02])

    # 앞쪽 관측이 없으면 0으로 채운다
    rf_late = pd.Series([4.0], index=pd.to_datetime(["2024-01-03"]))
    aligned_late = align_financing_rate(rf_late, idx, rate_scale="percent")
    np.testing.assert_allclose(aligned_late.to_numpy(), [0, 0, 0.04, 0.04, 0.04])

    # NAV에 반영: r=0, ER=0, L=2 → 일일 계수 1 - rf/252
    nav = simulate_leveraged(
        pd.Series(0.0, index=idx),
        leverage=2.0,
        expense_ratio=0.0,
        financing_rate=rf,
        start_value=1.0,
    )  # auto: max 9.0 > 1 → 퍼센트
    expected = np.cumprod(1 - np.array([0.05, 0.05, 0.02, 0.02, 0.02]) / 252)
    np.testing.assert_allclose(nav.to_numpy(), expected, rtol=1e-12)


def test_rate_scale_conventions():
    idx = _bdays(3)
    assert align_financing_rate(5.33, idx).iloc[0] == pytest.approx(0.0533)
    assert align_financing_rate(0.0533, idx).iloc[0] == pytest.approx(0.0533)
    low = pd.Series([0.08], index=idx[:1])  # 2021년 DFF 0.08%
    assert align_financing_rate(low, idx, "percent").iloc[-1] == pytest.approx(0.0008)
    assert align_financing_rate(low, idx, "decimal").iloc[-1] == pytest.approx(0.08)


def test_nav_floors_at_zero():
    rets = pd.Series([0.01, -0.40, 0.20, 0.30], index=_bdays(4))
    nav = simulate_leveraged(rets, leverage=3.0, expense_ratio=0.0)
    assert nav.iloc[0] == pytest.approx(103.0)
    assert (nav.iloc[1:] == 0.0).all()


def test_leading_nan_return_keeps_start_value():
    px = pd.Series([100.0, 101.0, 99.0], index=_bdays(3))
    nav = simulate_leveraged(px.pct_change(), leverage=3.0, expense_ratio=0.0)
    assert nav.iloc[0] == 100.0
    assert nav.iloc[1] == pytest.approx(103.0)


def test_calibrate_recovers_known_spread():
    rng = np.random.default_rng(42)
    n = 750
    idx = _bdays(n)
    rets = pd.Series(rng.normal(0.0005, 0.02, n), index=idx)
    rf = pd.Series(np.linspace(0.5, 5.0, n), index=idx)  # 퍼센트
    true_spread = 0.0125
    clean = simulate_leveraged(
        rets, expense_ratio=0.0095, financing_rate=rf, financing_spread=true_spread
    )
    # 일일 추적 노이즈 (평균 0, 5bp)
    noise = np.exp(np.cumsum(rng.normal(0.0, 0.0005, n)))
    actual = clean * noise
    res = calibrate(rets, actual, expense_ratio=0.0095, financing_rate=rf)
    assert abs(res.financing_spread - true_spread) < 0.0010
    assert res.n_days == n - 1
    assert res.daily_corr > 0.999
    assert res.annual_tracking_error == pytest.approx(0.0005 * np.sqrt(252), rel=0.15)


def test_calibrate_exact_and_missing_actual_days():
    rng = np.random.default_rng(7)
    n = 300
    idx = _bdays(n)
    rets = pd.Series(rng.normal(0.0, 0.015, n), index=idx)
    actual = simulate_leveraged(rets, financing_rate=0.03, financing_spread=-0.004)
    # 실제 시리즈에 결측일이 있어도 지수 수익률은 복리로 반영돼야 한다
    actual = actual.drop(idx[[10, 50, 51, 200]])
    res = calibrate(rets, actual, financing_rate=0.03)
    assert res.financing_spread == pytest.approx(-0.004, abs=1e-6)
    assert res.annual_tracking_error < 1e-8


def test_forward_returns():
    nav = pd.Series([100.0, 110.0, 121.0, 90.0, 99.0], index=_bdays(5))
    fr = forward_returns(nav, horizons=(1, 3))
    assert fr.shape == (5, 2)
    assert list(fr.columns) == ["fwd_1", "fwd_3"]
    assert fr["fwd_1"].iloc[0] == pytest.approx(0.10)
    assert fr["fwd_3"].iloc[0] == pytest.approx(-0.10)
    assert fr["fwd_3"].iloc[1] == pytest.approx(99.0 / 110.0 - 1)
    assert fr["fwd_3"].iloc[2:].isna().all()
    assert fr["fwd_1"].iloc[-1:].isna().all()

    default = forward_returns(pd.Series(1.0, index=_bdays(300)))
    assert default.shape == (300, 4)
    assert default["fwd_252"].notna().sum() == 48


@pytest.mark.network
def test_soxx_soxl_calibration_network(request):
    # conftest 없이 단독 실행해도 기본 실행에서는 건너뛴다.
    if "network" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("네트워크 테스트: -m network 로 실행")
    yf = pytest.importorskip("yfinance")

    def adj(sym: str) -> pd.Series:
        df = yf.download(
            sym, start="2021-01-01", end="2026-01-01", auto_adjust=False, progress=False
        )
        s = df["Adj Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = pd.DatetimeIndex(s.index).tz_localize(None)
        return s.dropna()

    soxx, soxl = adj("SOXX"), adj("SOXL")
    assert len(soxx) > 1000 and len(soxl) > 1000
    res = calibrate(soxx.pct_change(), soxl, leverage=3.0, financing_rate=0.0)
    print(f"\nSOXX->SOXL 2021-2025 rf=0: {res}")
    assert res.daily_corr > 0.99
    assert res.annual_tracking_error < 0.10
