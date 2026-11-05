from pathlib import Path

import pandas as pd
import pytest

import stocker.db
from stocker.ingest import fred as fred_mod
from stocker.ingest import prices as prices_mod

FIXTURES = Path(__file__).parent / "fixtures"
PRICE_COLS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]


def _yf_raw(ticker_first: bool = True) -> pd.DataFrame:
    """yfinance.download가 돌려주는 모양의 멀티인덱스 컬럼 DataFrame."""
    long = pd.read_csv(FIXTURES / "ingest_yf_raw.csv", parse_dates=["date"])
    wide = long.pivot(index="date", columns="symbol")  # (field, ticker)
    wide.index = wide.index.tz_localize("America/New_York")  # tz-aware 입력 처리 검증
    if ticker_first:
        wide = wide.swaplevel(axis=1).sort_index(axis=1)
    return wide


@pytest.fixture
def fake_yf(monkeypatch):
    calls = {}

    def _install(raw):
        def download(tickers, **kwargs):
            calls["tickers"] = tickers
            calls["kwargs"] = kwargs
            return raw

        monkeypatch.setattr(prices_mod.yf, "download", download)
        return calls

    return _install


@pytest.mark.parametrize("ticker_first", [True, False])
def test_fetch_prices_long_format(fake_yf, ticker_first):
    calls = fake_yf(_yf_raw(ticker_first))
    df = prices_mod.fetch_prices(["AAA", "BBB"], "2024-01-01", "2024-01-31")

    assert list(df.columns) == PRICE_COLS
    assert calls["kwargs"]["auto_adjust"] is False
    assert calls["kwargs"]["group_by"] == "ticker"
    assert calls["kwargs"]["threads"] is False
    # end는 포함 의미이므로 yfinance에는 하루 뒤(배타적)를 넘긴다
    assert calls["kwargs"]["end"] == pd.Timestamp("2024-02-01")

    # 결측 행(AAA 01-04) 제거, BBB는 원래 3행
    assert df.groupby("symbol").size().to_dict() == {"AAA": 3, "BBB": 3}
    assert not df.isna().any().any()
    assert df["date"].dtype.kind == "M"
    assert df["date"].dt.tz is None
    assert (df["date"] == df["date"].dt.normalize()).all()
    for c in ["open", "high", "low", "close", "adj_close"]:
        assert df[c].dtype == "float64"
    assert df["volume"].dtype == "int64"
    assert set(df["source"]) == {"yfinance"}

    row = df[(df.symbol == "BBB") & (df.date == "2024-01-03")].iloc[0]
    assert (row.open, row.close, row.adj_close, row.volume) == (20.5, 21.0, 20.6, 5500)


def test_fetch_prices_single_symbol_flat_columns(fake_yf):
    raw = _yf_raw()["AAA"]  # 평평한 컬럼
    fake_yf(raw)
    df = prices_mod.fetch_prices(["AAA"])
    assert len(df) == 3
    assert set(df["symbol"]) == {"AAA"}


def test_fetch_prices_skips_symbol_without_data(fake_yf, capsys):
    fake_yf(_yf_raw())
    df = prices_mod.fetch_prices(["AAA", "ZZZ"])
    assert set(df["symbol"]) == {"AAA"}
    assert "ZZZ" in capsys.readouterr().err


def test_fetch_prices_empty(fake_yf):
    fake_yf(pd.DataFrame())
    df = prices_mod.fetch_prices(["AAA"])
    assert df.empty
    assert list(df.columns) == PRICE_COLS


def test_ingest_prices_calls_upsert(fake_yf, monkeypatch):
    fake_yf(_yf_raw())
    seen = {}

    def fake_upsert(df, table, pk, engine=None):
        seen.update(df=df, table=table, pk=pk, engine=engine)
        return len(df)

    monkeypatch.setattr(stocker.db, "upsert_df", fake_upsert)
    n = prices_mod.ingest_prices(["AAA", "BBB"], start="2024-01-01", engine="ENG")
    assert n == 6
    assert seen["table"] == "prices_daily"
    assert seen["pk"] == ["symbol", "date"]
    assert seen["engine"] == "ENG"
    assert list(seen["df"].columns) == PRICE_COLS


def test_default_symbols():
    assert prices_mod.DEFAULT_SYMBOLS == ["^SOX", "SOXX", "SOXL", "SMH"]


class _FakeFred:
    def __init__(self, api_key):
        self.api_key = api_key

    def get_series(self, series_id, observation_start=None, observation_end=None):
        long = pd.read_csv(FIXTURES / "ingest_fred_raw.csv", parse_dates=["date"])
        g = long[long.series_id == series_id]
        return pd.Series(g["value"].to_numpy(), index=pd.DatetimeIndex(g["date"]))


@pytest.fixture
def fake_fred(monkeypatch):
    monkeypatch.setattr(fred_mod.settings, "fred_api_key", "test-key")
    monkeypatch.setattr(fred_mod, "Fred", _FakeFred)


def test_fetch_fred_long_format(fake_fred):
    df = fred_mod.fetch_fred(["DFF", "CPIAUCSL"])
    assert list(df.columns) == ["series_id", "date", "value", "source"]
    # DFF 결측 1행 생략, 월간 CPI는 원본 빈도 유지
    assert df.groupby("series_id").size().to_dict() == {"CPIAUCSL": 2, "DFF": 3}
    assert not df.isna().any().any()
    assert df["date"].dtype.kind == "M"
    assert df["value"].dtype == "float64"
    assert set(df["source"]) == {"fred"}


def test_fetch_fred_without_key_raises(monkeypatch):
    monkeypatch.setattr(fred_mod.settings, "fred_api_key", None)
    with pytest.raises(RuntimeError, match="FRED_API_KEY"):
        fred_mod.fetch_fred(["DFF"])


def test_ingest_fred_calls_upsert(fake_fred, monkeypatch):
    seen = {}

    def fake_upsert(df, table, pk, engine=None):
        seen.update(table=table, pk=pk, engine=engine)
        return len(df)

    monkeypatch.setattr(stocker.db, "upsert_df", fake_upsert)
    assert fred_mod.ingest_fred(["DFF", "CPIAUCSL"], engine="ENG") == 5
    assert seen == {"table": "macro_series", "pk": ["series_id", "date"], "engine": "ENG"}


def test_cli_fred_without_key_exits_1(monkeypatch):
    from typer.testing import CliRunner

    from stocker.ingest.__main__ import app

    monkeypatch.setattr(fred_mod.settings, "fred_api_key", None)
    result = CliRunner().invoke(app, ["fred"])
    assert result.exit_code == 1
    assert "FRED_API_KEY" in result.output


@pytest.mark.network
def test_fetch_prices_network():
    df = prices_mod.fetch_prices(["SOXX"], "2024-01-01", "2024-01-31")
    assert list(df.columns) == PRICE_COLS
    assert 19 <= len(df) <= 22
    assert set(df["symbol"]) == {"SOXX"}


@pytest.mark.db
def test_ingest_prices_roundtrip_db(engine, fake_yf):
    from stocker.db import init_db, read_prices

    init_db(engine)
    sym = "TEST_INGEST"
    raw = _yf_raw().rename(columns={"AAA": sym, "BBB": "TEST_INGEST_B"})
    fake_yf(raw)
    try:
        n = prices_mod.ingest_prices([sym, "TEST_INGEST_B"], engine=engine)
        assert n == 6
        got = read_prices(sym, engine=engine)
        want = prices_mod.fetch_prices([sym, "TEST_INGEST_B"]).query("symbol == @sym")
        want = want.set_index("date")[["open", "high", "low", "close", "adj_close", "volume"]]
        want.index = want.index.astype(got.index.dtype)  # 시간 해상도(s/us/ns)는 무시
        pd.testing.assert_frame_equal(got, want, check_freq=False, check_dtype=False)
        # 재실행해도 행이 늘지 않는다 (upsert)
        prices_mod.ingest_prices([sym, "TEST_INGEST_B"], engine=engine)
        assert len(read_prices(sym, engine=engine)) == 3
    finally:
        import sqlalchemy as sa

        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM prices_daily WHERE symbol IN (:a, :b)"),
                {"a": sym, "b": "TEST_INGEST_B"},
            )
