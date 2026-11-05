import importlib.util
from pathlib import Path

import pandas as pd
import pytest

import stocker.db
from stocker.ingest import indicators as ind

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LONG_COLS = ["series_id", "date", "value", "source"]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _shiller_row(date, price=None, earn=None, cape=None):
    row = [""] * 22
    row[0], row[7], row[10], row[12] = date, price, earn, cape
    return row


def test_parse_shiller_fixture():
    parse = _load_script("parse_shiller").parse_ie_data
    rows = [
        ["Date", "P", "D", "E"],  # 헤더(문자열) 무시
        _shiller_row(1871.01, 100.0, 10.0, "NA"),  # CAPE 없음, E/P만
        _shiller_row(1871.1, 80.0, 4.0, 20.0),  # 10월은 .1 로 표기
        _shiller_row(2026.07, 7000.0, "", 40.0),  # 이익 미발표: E/P 생략, CAPE만
        _shiller_row(2026.08, "", "", ""),  # 둘 다 없음: 행 생략
        ["", "Oct price is Oct 2nd close"],  # 하단 주석
    ]
    df = parse(rows)

    assert list(df.columns) == ["date", "cape", "ep"]
    assert df["date"].tolist() == [
        pd.Timestamp("1871-01-31"),
        pd.Timestamp("1871-10-31"),
        pd.Timestamp("2026-07-31"),
    ]
    assert pd.isna(df.loc[0, "cape"]) and df.loc[0, "ep"] == pytest.approx(0.1)
    assert df.loc[1, "cape"] == 20.0 and df.loc[1, "ep"] == pytest.approx(0.05)
    assert df.loc[2, "cape"] == 40.0 and pd.isna(df.loc[2, "ep"])


def test_parse_ritter_fixture():
    parse = _load_script("parse_ritter").parse_ipoall
    rows = [
        (3, 79, 9.9, 5, 5, None, "설명 텍스트"),  # start_year 이전: 제외
        (12, 99, 69.7, 90, 70, 40, None),  # 두 자리 연도 99 → 1999
        (3, 0, 17.6, 80, 60, 30, None),  # 00 → 2000
        (4, 0, ".", 0, 0, None, None),  # IPO 없는 달
        (12, 25, 10.6, 33, 7, 43, None),
        (None, None, None, None, None, None, None),
        ("the first column is the month", None, None, None, None, None, None),
    ]
    df = parse(rows, start_year=1980)

    assert df["date"].tolist() == [
        pd.Timestamp("1999-12-31"),
        pd.Timestamp("2000-03-31"),
        pd.Timestamp("2025-12-31"),
    ]
    assert df["value"].tolist() == [69.7, 17.6, 10.6]
    assert (df["freq"] == "monthly").all()


def _fred_long(rows):
    return pd.DataFrame(rows, columns=["series_id", "date", "value"]).assign(source="fred")


def test_compute_capex_ratio():
    q1, q2 = pd.Timestamp("2000-01-01"), pd.Timestamp("2000-04-01")
    fred = _fred_long(
        [
            (ind.CAPEX_NUMERATOR, q1, 400.0),
            (ind.CAPEX_DENOMINATOR, q1, 10000.0),
            (ind.CAPEX_NUMERATOR, q2, 440.0),
            (ind.CAPEX_DENOMINATOR, q2, 10000.0),
            (ind.CAPEX_NUMERATOR, pd.Timestamp("2000-07-01"), 450.0),  # GDP 없음: 제외
        ]
    )
    df = ind.compute_capex_ratio(fred)

    assert list(df.columns) == LONG_COLS
    assert (df["series_id"] == "CAPEX_IPS_GDP").all() and (df["source"] == "fred").all()
    assert df["date"].tolist() == [pd.Timestamp("2000-03-31"), pd.Timestamp("2000-06-30")]
    assert df["value"].tolist() == pytest.approx([0.04, 0.044])


def test_compute_capex_ratio_missing_series():
    fred = _fred_long([(ind.CAPEX_NUMERATOR, pd.Timestamp("2000-01-01"), 1.0)])
    with pytest.raises(RuntimeError, match="GDP"):
        ind.compute_capex_ratio(fred)


def test_expand_annual_to_monthly():
    annual = pd.DataFrame(
        {"date": pd.to_datetime(["1999-12-31", "2000-12-31"]), "value": [71.1, 56.3]}
    )
    m = ind.expand_annual_to_monthly(annual)

    assert len(m) == 24
    assert m["date"].iloc[0] == pd.Timestamp("1999-01-31")
    assert m["date"].iloc[-1] == pd.Timestamp("2000-12-31")
    assert (m["date"] == m["date"] + pd.offsets.MonthEnd(0)).all()
    assert m["value"].iloc[:12].eq(71.1).all() and m["value"].iloc[12:].eq(56.3).all()


def test_load_ritter_monthly_and_annual(tmp_path):
    path = tmp_path / "ritter.csv"
    path.write_text(
        "# source: http://example.com, fetched: 2026-01-01\n"
        "date,value,freq\n"
        "1999-12-15,69.7,monthly\n"  # 월중 날짜도 월말로
        "2000-12-31,56.3,annual\n"
        "2000-03-31,17.6,monthly\n"  # 2000년 3월은 월간이 연간보다 우선
        "2001-05-31,,monthly\n",  # 결측 행 생략
        encoding="utf-8",
    )
    df = ind.load_ritter(path)

    assert list(df.columns) == LONG_COLS
    assert (df["series_id"] == "IPO_FIRSTDAY").all()
    assert df["date"].is_unique and df["date"].is_monotonic_increasing
    by_date = df.set_index("date")
    assert by_date.loc["1999-12-31", "source"] == "ritter_monthly"
    assert by_date.loc["2000-03-31", ["value", "source"]].tolist() == [17.6, "ritter_monthly"]
    assert by_date.loc["2000-04-30", ["value", "source"]].tolist() == [56.3, "ritter_annual"]
    assert len(df) == 1 + 12  # 1999-12 한 달 + 2000년 12개월


def test_load_shiller(tmp_path):
    path = tmp_path / "shiller.csv"
    path.write_text(
        "# source: http://example.com, fetched: 2026-01-01\n"
        "date,cape,ep\n"
        "2000-03-31,43.8,0.034\n"
        "2026-10-31,40.7,\n",  # E/P 미발표
        encoding="utf-8",
    )
    df = ind.load_shiller(path)

    assert list(df.columns) == LONG_COLS
    got = {(r.series_id, r.date.strftime("%Y-%m-%d")): r.value for r in df.itertuples()}
    assert got == {
        ("SHILLER_CAPE", "2000-03-31"): 43.8,
        ("SHILLER_CAPE", "2026-10-31"): 40.7,
        ("SHILLER_EP", "2000-03-31"): 0.034,
    }
    assert (df["source"] == "shiller").all()


def test_load_missing_seed_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="seed-indicators"):
        ind.load_shiller(tmp_path / "nope.csv")


def test_fetch_indicators_isolates_failures(monkeypatch):
    ok = pd.DataFrame(
        {
            "series_id": ["SHILLER_CAPE"],
            "date": [pd.Timestamp("2000-03-31")],
            "value": [43.8],
            "source": ["shiller"],
        }
    )

    def boom():
        raise RuntimeError("FRED 장애")

    monkeypatch.setattr(ind, "fetch_capex_ratio", boom)
    monkeypatch.setattr(ind, "load_shiller", lambda: ok)
    monkeypatch.setattr(ind, "load_ritter", lambda: (_ for _ in ()).throw(FileNotFoundError("x")))
    df = ind.fetch_indicators()

    assert df["series_id"].tolist() == ["SHILLER_CAPE"]  # 둘이 실패해도 나머지는 남는다


def test_ingest_indicators_upserts_macro_series(monkeypatch):
    sample = pd.DataFrame(
        {
            "series_id": ["IPO_FIRSTDAY"],
            "date": [pd.Timestamp("2000-03-31")],
            "value": [17.6],
            "source": ["ritter_monthly"],
        }
    )
    calls = []

    def fake_upsert(df, table, pk, engine=None):
        calls.append((df, table, pk, engine))
        return len(df)

    monkeypatch.setattr(ind, "fetch_indicators", lambda: sample)
    monkeypatch.setattr(stocker.db, "upsert_df", fake_upsert)

    assert ind.ingest_indicators(engine="ENG") == 1
    df, table, pk, engine = calls[0]
    assert table == "macro_series" and pk == ["series_id", "date"] and engine == "ENG"
    assert df.equals(sample)


@pytest.mark.network
def test_capex_ratio_from_fred_network():
    df = ind.fetch_capex_ratio()
    assert df["date"].min() <= pd.Timestamp("1947-03-31")
    assert df["value"].between(0.003, 0.1).all()  # GDP의 0.3~10% (1949년 0.6%, 2026년 5.3%)
    assert (df["date"] == df["date"] + pd.offsets.QuarterEnd(0)).all()
