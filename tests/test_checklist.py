"""체크리스트 규칙 (계약 PHASE6C 30절): 경계값, 데이터 없음, 45일 지연, 룩어헤드 금지."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocker.checklist import RULES, evaluate

D = pd.Timestamp("2000-03-10")


def _daily(value, end=D, n=300):
    idx = pd.bdate_range(end=end, periods=n)
    return pd.Series(value, index=idx, dtype="float64")


def _monthly(value, last="2000-01-31", n=24):
    idx = pd.date_range(end=last, periods=n, freq="ME")
    return pd.Series(value, index=idx, dtype="float64")


def _close(ret=0.0, end=D, n=300):
    """마지막 252거래일 전 대비 수익률이 정확히 ret이 되는 종가."""
    idx = pd.bdate_range(end=end, periods=n)
    v = np.full(n, 100.0)
    v[-1] = 100.0 * (1 + ret)
    return pd.Series(v, index=idx)


def _sig(signals, sid):
    return next(s for s in signals if s.id == sid)


def _base(**over):
    """모든 신호가 꺼지는 기준 입력."""
    series = {
        "DGS10": _daily(6.0),
        "DFF": _daily(5.0),
        "T10Y2Y": _daily(0.5),
        "SHILLER_EP": _monthly(0.05),
        "SHILLER_CAPE": _monthly(20.0),
        "IPO_FIRSTDAY": _monthly(10.0),
        "BAMLH0A0HYM2": _daily(4.0),
        "BAMLH0A3HYC": _daily(8.0),
    }
    series.update(over)
    return series


def test_nine_signals_all_off_on_baseline():
    sig = evaluate(D, _base(), _close())
    assert len(sig) == len(RULES) == 9
    assert [s.on for s in sig] == [False] * 9
    assert all(s.value is not None for s in sig)


@pytest.mark.parametrize(
    ("sid", "series_id", "on_value", "off_value"),
    [
        ("bubble_inversion", "T10Y2Y", -0.01, 0.0),  # 엄격한 <
        ("crisis_hy", "BAMLH0A0HYM2", 7.0, 6.99),  # ≥ 경계 포함
        ("crisis_ccc", "BAMLH0A3HYC", 12.0, 11.99),
    ],
)
def test_daily_threshold_boundaries(sid, series_id, on_value, off_value):
    assert _sig(evaluate(D, _base(**{series_id: _daily(on_value)}), _close()), sid).on is True
    assert _sig(evaluate(D, _base(**{series_id: _daily(off_value)}), _close()), sid).on is False


@pytest.mark.parametrize(
    ("sid", "series_id", "on_value", "off_value"),
    [
        ("bubble_ipo", "IPO_FIRSTDAY", 50.0, 49.9),
        ("heat_cape", "SHILLER_CAPE", 35.0, 34.9),
    ],
)
def test_lagged_threshold_boundaries(sid, series_id, on_value, off_value):
    assert _sig(evaluate(D, _base(**{series_id: _monthly(on_value)}), _close()), sid).on is True
    assert _sig(evaluate(D, _base(**{series_id: _monthly(off_value)}), _close()), sid).on is False


def test_yield_spread_boundary():
    # EP 3.3% − 10년물 6.0% = −2.7%p → 켜짐(≤). 부동소수 잡음이 있어도 경계에서 켜진다.
    s = evaluate(D, _base(SHILLER_EP=_monthly(0.033)), _close())
    sig = _sig(s, "bubble_yield_spread")
    assert sig.on is True and sig.value == pytest.approx(-2.7)
    sig = _sig(evaluate(D, _base(SHILLER_EP=_monthly(0.034)), _close()), "bubble_yield_spread")
    assert sig.on is False and sig.value == pytest.approx(-2.6)


def test_sox_one_year_boundary():
    assert _sig(evaluate(D, _base(), _close(1.0)), "heat_sox_1y").on is True
    assert _sig(evaluate(D, _base(), _close(0.99)), "heat_sox_1y").on is False
    # 253개 미만이면 데이터 없음
    assert _sig(evaluate(D, _base(), _close(1.0, n=252)), "heat_sox_1y").on is None


def test_10y_turn_boundary():
    def with_drop(drop):
        v = np.full(300, 6.0)
        v[-1] = 6.0 - drop
        return pd.Series(v, index=pd.bdate_range(end=D, periods=300))

    assert _sig(evaluate(D, _base(DGS10=with_drop(0.4)), _close()), "bottom_10y_turn").on is True
    assert _sig(evaluate(D, _base(DGS10=with_drop(0.39)), _close()), "bottom_10y_turn").on is False
    # 최고점이 60거래일 밖이면 쓰이지 않는다
    v = np.full(300, 5.0)
    v[-61] = 9.0
    old_peak = pd.Series(v, index=pd.bdate_range(end=D, periods=300))
    assert _sig(evaluate(D, _base(DGS10=old_peak), _close()), "bottom_10y_turn").on is False
    v[-60] = 9.0  # 60거래일 창의 첫 칸
    in_window = pd.Series(v, index=old_peak.index)
    assert _sig(evaluate(D, _base(DGS10=in_window), _close()), "bottom_10y_turn").on is True


def test_fed_cut_boundary():
    def dff(change):
        idx = pd.bdate_range(end=D, periods=300)
        v = np.full(300, 5.0)
        v[idx > D - pd.Timedelta(days=30)] = 5.0 + change
        return pd.Series(v, index=idx)

    assert _sig(evaluate(D, _base(DFF=dff(-0.20)), _close()), "bottom_fed_cut").on is True
    assert _sig(evaluate(D, _base(DFF=dff(-0.19)), _close()), "bottom_fed_cut").on is False
    assert _sig(evaluate(D, _base(DFF=dff(0.25)), _close()), "bottom_fed_cut").on is False


def test_missing_series_gives_none():
    series = _base()
    for sid in ("SHILLER_CAPE", "IPO_FIRSTDAY", "BAMLH0A0HYM2"):
        del series[sid]
    sig = {s.id: s for s in evaluate(D, series, _close())}
    for sid in ("heat_cape", "bubble_ipo", "crisis_hy"):
        assert sig[sid].on is None and sig[sid].value is None
    assert sig["bubble_inversion"].on is False  # 있는 것은 평가된다
    # 일드 스프레드는 EP나 DGS10 중 하나만 없어도 데이터 없음
    del series["SHILLER_EP"]
    assert _sig(evaluate(D, series, _close()), "bubble_yield_spread").on is None


def test_no_value_before_date_gives_none():
    # 시리즈가 date 이후에 시작하면 date 시점에는 값이 없다
    later = _daily(9.0, end=D + pd.Timedelta(days=400), n=200)
    assert _sig(evaluate(D, _base(BAMLH0A0HYM2=later), _close()), "crisis_hy").on is None
    assert _sig(evaluate(D, _base(), pd.Series(dtype=float)), "heat_sox_1y").on is None


def test_stale_series_gives_none():
    old = _daily(9.0, end=D - pd.Timedelta(days=30))
    assert _sig(evaluate(D, _base(BAMLH0A0HYM2=old), _close()), "crisis_hy").on is None


def test_monthly_series_lag_45_days():
    # 2000-01-31 값은 45일 뒤인 2000-03-16부터 보인다
    cape = pd.Series([40.0], index=[pd.Timestamp("2000-01-31")])
    before = evaluate(
        pd.Timestamp("2000-03-15"), _base(SHILLER_CAPE=cape), _close(end="2000-03-15")
    )
    after = evaluate(pd.Timestamp("2000-03-16"), _base(SHILLER_CAPE=cape), _close(end="2000-03-16"))
    assert _sig(before, "heat_cape").on is None  # 그 전 값이 없으니 데이터 없음
    assert _sig(after, "heat_cape").on is True and _sig(after, "heat_cape").value == 40.0
    # 다음 달 값(2월 말)은 4월 14일부터 보이고, 그 전까지는 1월 말 값을 이어 쓴다(ffill)
    two = pd.Series([20.0, 40.0], index=pd.to_datetime(["2000-01-31", "2000-02-29"]))

    def cape_at(day):
        c = _close(end=day)
        return _sig(evaluate(pd.Timestamp(day), _base(SHILLER_CAPE=two), c), "heat_cape")

    assert cape_at("2000-04-13").value == 20.0 and cape_at("2000-04-13").on is False
    assert cape_at("2000-04-14").value == 40.0 and cape_at("2000-04-14").on is True


def test_no_lookahead_future_data_is_ignored():
    series = _base()
    base = evaluate(D, series, _close())
    # date 이후 값을 극단값으로 바꾸거나 붙여도 결과가 같다
    future_idx = pd.bdate_range(D + pd.Timedelta(days=1), periods=60)
    changed = {}
    for sid, s in series.items():
        extra = pd.Series(-99.0 if sid == "T10Y2Y" else 999.0, index=future_idx)
        changed[sid] = pd.concat([s, extra])
    changed["SHILLER_CAPE"] = pd.concat(
        [series["SHILLER_CAPE"], pd.Series([99.0], index=[pd.Timestamp("2000-03-01")])]
    )  # 지연 후에도 date 이후(2000-04-15)
    close = _close()
    close_future = pd.concat([close, pd.Series(1e6, index=future_idx)])
    assert evaluate(D, changed, close_future) == base
