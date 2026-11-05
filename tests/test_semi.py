import pandas as pd
import pytest
import sqlalchemy as sa

import stocker.db
from stocker.ingest import semi as semi_mod

LONG_COLS = ["metric", "date", "value", "source"]


def _write_seed(tmp_path, body: str):
    path = tmp_path / "seed.csv"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_wsts_seed_comment_and_month_end(tmp_path):
    path = _write_seed(
        tmp_path,
        "# source: http://example.com, fetched: 2026-01-01\n"
        "date,value\n"
        "2024-01-31,100.5\n"
        "2024-02-15,110.0\n"  # 월중 날짜도 월말로
        "2024-03-31,\n",  # 결측 값은 행 생략
    )
    df = semi_mod.load_wsts_seed(path)

    assert list(df.columns) == LONG_COLS
    assert len(df) == 2
    assert (df["metric"] == "wsts_sales_world_usd_m").all()
    assert (df["source"] == "wsts").all()
    assert df["date"].dt.tz is None
    assert df["date"].tolist() == [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")]
    assert df["value"].dtype == "float64"


def test_repo_seed_is_sane():
    df = semi_mod.load_wsts_seed()
    assert df["date"].min() == pd.Timestamp("1986-01-31")
    assert len(df) > 400
    assert df["date"].is_unique and df["date"].is_monotonic_increasing
    assert (df["date"] == df["date"] + pd.offsets.MonthEnd(0)).all()
    assert df["value"].between(1_000, 1_000_000).all()  # 백만 달러 단위


def _fake_stmt(columns, revenue, gross):
    return pd.DataFrame(
        {c: [r, g, 1.0] for c, r, g in zip(columns, revenue, gross, strict=True)},
        index=["Total Revenue", "Gross Profit", "Other"],
    )


def test_fetch_mu_financials(monkeypatch):
    cols = pd.to_datetime(["2025-11-30", "2025-08-31", "2025-05-31"])
    stmt = _fake_stmt(cols, [13.643e9, 11.315e9, 9.301e9], [7.646e9, 5.054e9, 3.5e9])

    class FakeTicker:
        quarterly_financials = stmt
        quarterly_income_stmt = stmt

    monkeypatch.setattr(semi_mod.yf, "Ticker", lambda sym: FakeTicker())
    df = semi_mod.fetch_mu_financials()

    assert list(df.columns) == LONG_COLS
    assert set(df["metric"]) == {"mu_revenue_usd_m", "mu_gross_margin"}
    assert (df["source"] == "yfinance").all()
    rev = df[df["metric"] == "mu_revenue_usd_m"].set_index("date")["value"]
    margin = df[df["metric"] == "mu_gross_margin"].set_index("date")["value"]
    assert rev[pd.Timestamp("2025-11-30")] == pytest.approx(13643.0)
    assert margin[pd.Timestamp("2025-11-30")] == pytest.approx(7.646 / 13.643)
    assert margin.between(0, 1).all()
    assert len(rev) == len(margin) == 3


def test_fetch_mu_financials_falls_back_to_income_stmt(monkeypatch):
    cols = pd.to_datetime(["2025-11-30"])
    stmt = _fake_stmt(cols, [10e9], [4e9])

    class FakeTicker:
        quarterly_financials = pd.DataFrame()
        quarterly_income_stmt = stmt

    monkeypatch.setattr(semi_mod.yf, "Ticker", lambda sym: FakeTicker())
    df = semi_mod.fetch_mu_financials()
    assert len(df) == 2


def test_fetch_mu_financials_empty(monkeypatch):
    class FakeTicker:
        quarterly_financials = pd.DataFrame()
        quarterly_income_stmt = pd.DataFrame()

    monkeypatch.setattr(semi_mod.yf, "Ticker", lambda sym: FakeTicker())
    df = semi_mod.fetch_mu_financials()
    assert df.empty
    assert list(df.columns) == LONG_COLS


def test_ingest_semi_calls_upsert(monkeypatch):
    wsts = pd.DataFrame(
        {
            "metric": "wsts_sales_world_usd_m",
            "date": [pd.Timestamp("2024-01-31")],
            "value": [1.0],
            "source": "wsts",
        }
    )
    mu = pd.DataFrame(
        {
            "metric": "mu_revenue_usd_m",
            "date": [pd.Timestamp("2025-11-30")],
            "value": [2.0],
            "source": "yfinance",
        }
    )
    monkeypatch.setattr(semi_mod, "load_wsts_seed", lambda: wsts)
    monkeypatch.setattr(semi_mod, "fetch_mu_financials", lambda: mu)
    calls = {}

    def fake_upsert(df, table, pk, engine=None):
        calls.update(df=df, table=table, pk=pk, engine=engine)
        return len(df)

    monkeypatch.setattr(stocker.db, "upsert_df", fake_upsert)
    assert semi_mod.ingest_semi(engine="ENG") == 2
    assert calls["table"] == "semi_cycle"
    assert calls["pk"] == ["metric", "date"]
    assert calls["engine"] == "ENG"
    assert set(calls["df"]["metric"]) == {"wsts_sales_world_usd_m", "mu_revenue_usd_m"}


@pytest.mark.db
def test_ingest_and_read_cycle(engine):
    stocker.db.init_db(engine)
    seed = semi_mod.load_wsts_seed()
    test_metric = "test_metric"
    extra = pd.DataFrame(
        {
            "metric": test_metric,
            "date": pd.to_datetime(["2024-03-31", "2024-06-30"]),
            "value": [0.4, 0.5],
            "source": "test",
        }
    )

    def wipe():
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM semi_cycle WHERE metric = :m"), {"m": test_metric})

    wipe()
    try:
        assert semi_mod.store_semi(pd.concat([seed, extra], ignore_index=True), engine=engine) > 0

        s = stocker.db.read_cycle("wsts_sales_world_usd_m", engine=engine)
        assert s.name == "wsts_sales_world_usd_m"
        assert s.index.tz is None
        assert len(s) == len(seed)
        assert s.iloc[0] == pytest.approx(seed["value"].iloc[0])
        assert s.index[0] == seed["date"].iloc[0]

        part = stocker.db.read_cycle("wsts_sales_world_usd_m", "2020-01-01", "2020-12-31", engine)
        assert len(part) == 12

        t = stocker.db.read_cycle(test_metric, engine=engine)
        assert t.tolist() == [0.4, 0.5]

        # 재적재는 중복 행을 만들지 않는다
        semi_mod.store_semi(extra, engine=engine)
        assert len(stocker.db.read_cycle(test_metric, engine=engine)) == 2
    finally:
        wipe()
