"""charts.py가 합성 데이터로 Figure를 만드는지 확인한다 (렌더링·DB·streamlit 없음)."""

import sys

import numpy as np
import pandas as pd
import pytest

from stocker.similarity.engine import Match
from stocker.web import charts

WINDOW = 30
EXTEND = 40


@pytest.fixture(scope="module")
def close() -> pd.Series:
    idx = pd.bdate_range("2015-01-01", periods=900)
    rng = np.random.default_rng(0)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, len(idx)))), index=idx)


@pytest.fixture(scope="module")
def matches(close) -> list[Match]:
    out = []
    for pos in (200, 400, 600):
        end = close.index[pos]
        fwd = {h: float(close.iloc[pos + h] / close.iloc[pos] - 1) for h in (21, 63, 126, 252)}
        out.append(
            Match(
                end_date=end,
                scores={"price_shape": 0.9, "semi_cycle": 0.5},
                combined=0.8,
                forward=fwd,
                start_date=close.index[pos - WINDOW + 1],
            )
        )
    return out


@pytest.fixture(scope="module")
def paths(close, matches) -> pd.DataFrame:
    return charts.build_overlay(close, matches, close.index[-1], WINDOW, extend=EXTEND)


def _count(fig, meta):
    return sum(1 for t in fig.data if t.meta == meta)


def test_overlay_paths_shape(paths):
    assert set(paths["role"]) == {"current", "past"}
    cur = paths[paths["role"] == "current"]
    assert len(cur) == WINDOW
    assert cur["level"].iloc[0] == pytest.approx(100.0)
    for _, g in paths[paths["role"] == "past"].groupby("label"):
        assert len(g) == WINDOW + EXTEND
        assert g["level"].iloc[0] == pytest.approx(100.0)
        assert g["day"].iloc[-1] == WINDOW + EXTEND - 1


def test_overlay_figure_without_events(paths):
    fig = charts.overlay_figure(paths, WINDOW, None)
    assert _count(fig, "current") == 1
    assert _count(fig, "past") == 3
    assert _count(fig, "future") == 3
    assert _count(fig, "event") == 0
    assert len(fig.data) == 1 + 3 + 3


def test_overlay_figure_with_events(close, matches, paths):
    d0 = paths[paths["role"] == "current"]["date"].iloc[5]
    events = pd.DataFrame(
        {
            "date": [d0, d0 + pd.Timedelta(days=1), pd.Timestamp("2099-01-01")],
            "kind": ["fomc", "policy", "war"],
            "title": ["FOMC 회의", "정책", "범위 밖"],
            "symbol": [None, None, None],
            "scheduled": [True, False, False],
        }
    )
    pts = charts.attach_events(paths, events)
    assert set(pts["kind"]) == {"fomc", "policy"}  # 범위 밖 이벤트는 빠진다
    assert (pts["label"] == "현재").any()
    fig = charts.overlay_figure(paths, WINDOW, pts)
    assert _count(fig, "event") == 2  # kind마다 마커 trace 1개
    assert _count(fig, "current") == 1
    assert _count(fig, "past") == 3
    only = charts.attach_events(paths, events, kinds=["fomc"])
    assert set(only["kind"]) == {"fomc"}


def test_attach_events_empty(paths):
    assert charts.attach_events(paths, None).empty
    assert charts.attach_events(paths, pd.DataFrame()).empty
    fig = charts.overlay_figure(paths, WINDOW, charts.attach_events(paths, None))
    assert _count(fig, "event") == 0


def test_forward_figure_and_tables(close, matches):
    horizons = (21, 63, 126, 252)
    fig = charts.forward_figure(matches, horizons, WINDOW, close.index)
    assert len(fig.data) == len(horizons)
    assert all(len(t.y) == 3 for t in fig.data)
    table = charts.matches_frame(matches, horizons, WINDOW, close.index)
    assert len(table) == 3
    assert {"구간", "가격 형태", "매크로", "산업 사이클", "종합"} <= set(table.columns)
    assert table["매크로"].isna().all()  # 꺼진 레이어는 NaN


def test_sim_with_and_without_actual(close, matches):
    rf = pd.Series(5.0, index=pd.bdate_range("2010-01-01", close.index[-1]))
    actual = close.iloc[300:] * 3  # 일부 창 구간만 '실제 SOXL'이 있는 상황
    paths, table = charts.build_sim(close, matches, WINDOW, actual, rf, extend=EXTEND)
    assert len(table) == 3
    assert list(table.columns) == charts.SIM_SUMMARY_COLS
    assert paths.groupby("label")["sim"].first().eq(100.0).all()
    assert paths["actual"].notna().any() and paths["actual"].isna().any()
    fig = charts.sim_figure(paths, WINDOW)
    assert _count(fig, "sim") == 3
    assert 1 <= _count(fig, "actual") <= 2  # 첫 창(200)은 실제 데이터가 없다

    paths0, table0 = charts.build_sim(close, matches, WINDOW, None, None, extend=EXTEND)
    assert paths0["actual"].isna().all()
    assert _count(charts.sim_figure(paths0, WINDOW), "actual") == 0
    # 금융비용이 있으면 없는 경우보다 최종값이 낮다
    col = "SOXL 시뮬 12개월 후 (%)"
    assert (table[col].to_numpy() < table0[col].to_numpy()).all()


def test_streamlit_not_imported():
    assert "stocker.web.charts" in sys.modules
    assert "streamlit" not in sys.modules
