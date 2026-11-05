import math

import pandas as pd
import pytest

from stocker.db import read_event_impact
from stocker.events.impact import (
    compute_event_impact,
    event_path,
    impact_from_close,
    path_from_close,
)

IDX = pd.bdate_range("2023-01-02", periods=100)
CLOSE = pd.Series([100.0 + i for i in range(100)], index=IDX)  # c[i] = 100 + i


def _row(dates):
    return impact_from_close(CLOSE, dates).iloc[0]


def test_trading_day_event_hand_computed():
    r = _row([IDX[20]])
    assert r["ret5"] == pytest.approx(125 / 115 - 1)  # 이후 5번째 / 이전 5번째
    assert r["ret_1m"] == pytest.approx(141 / 120 - 1)  # 21거래일 뒤 / 당일
    assert r["ret_3m"] == pytest.approx(183 / 120 - 1)  # 63거래일 뒤 / 당일


def test_holiday_event_uses_previous_trading_day():
    assert IDX[24].day_of_week == 4  # 금요일
    saturday = IDX[24] + pd.Timedelta(days=1)
    r = _row([saturday])
    assert r["ret5"] == pytest.approx(129 / 119 - 1)
    assert r["ret_1m"] == pytest.approx(145 / 124 - 1)
    assert r["ret_3m"] == pytest.approx(187 / 124 - 1)


def test_missing_trading_day_in_series_falls_back():
    close = CLOSE.drop(IDX[30])  # 휴장 한 날
    r = impact_from_close(close, [IDX[30]]).iloc[0]
    # 기준일은 IDX[29]. 시리즈에서 IDX[30]이 빠졌으므로 위치는 29 그대로
    assert r["ret_1m"] == pytest.approx(close.iloc[29 + 21] / close.iloc[29] - 1)


def test_insufficient_data_is_nan():
    early = _row([IDX[3]])  # 앞 5거래일 부족
    assert math.isnan(early["ret5"])
    assert early["ret_1m"] == pytest.approx(124 / 103 - 1)
    late = _row([IDX[90]])  # 뒤 데이터 부족
    assert math.isnan(late["ret_3m"]) and math.isnan(late["ret_1m"])
    assert late["ret5"] == pytest.approx(195 / 185 - 1)
    before_all = _row([IDX[0] - pd.Timedelta(days=10)])
    assert before_all.isna().all()


def test_event_path_length_and_base():
    p = path_from_close(CLOSE, IDX[50], days=30)
    assert len(p) == 61
    assert p.index.name == "d"
    assert list(p.index[[0, 30, -1]]) == [-30, 0, 30]
    assert p.loc[0] == pytest.approx(100.0)
    assert p.loc[5] == pytest.approx(155 / 150 * 100)


def test_event_path_truncates_at_data_edges():
    p = path_from_close(CLOSE, IDX[10], days=30)
    assert p.index.min() == -10 and p.index.max() == 30
    assert p.loc[0] == pytest.approx(100.0)
    assert path_from_close(CLOSE, IDX[0] - pd.Timedelta(days=5)).empty


@pytest.mark.db
def test_db_compute_and_read(engine):
    from stocker.db import init_db

    init_db(engine)
    n1 = compute_event_impact(engine=engine)
    n2 = compute_event_impact(engine=engine)
    assert n1 == n2 > 0
    top = read_event_impact(limit=5, engine=engine)
    assert list(top.columns) == [
        "id",
        "date",
        "kind",
        "title",
        "source",
        "ret5",
        "ret_1m",
        "ret_3m",
    ]
    assert top.iloc[0]["date"] == pd.Timestamp("2001-09-11")
    assert top.iloc[0]["ret5"] == pytest.approx(-0.33, abs=0.05)
    assert top["ret5"].abs().is_monotonic_decreasing
    assert len(read_event_impact(limit=500, engine=engine)) == n1
    path = event_path(int(top.iloc[0]["id"]), days=30, engine=engine)
    assert len(path) == 61 and path.loc[0] == pytest.approx(100.0)
    with pytest.raises(KeyError):
        event_path(-1, engine=engine)
