import pandas as pd
import pytest
import sqlalchemy as sa

from stocker.db import init_db, read_macro, read_prices, upsert_df

pytestmark = pytest.mark.db

SYMBOL = "TEST_SYM"
SERIES = "TEST_SERIES"


@pytest.fixture
def clean(engine):
    init_db(engine)

    def wipe():
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM prices_daily WHERE symbol = :s"), {"s": SYMBOL})
            conn.execute(sa.text("DELETE FROM macro_series WHERE series_id = :s"), {"s": SERIES})

    wipe()
    yield engine
    wipe()


def _prices(n=3, close0=10.0):
    dates = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame(
        {
            "symbol": SYMBOL,
            "date": dates,
            "open": close0,
            "high": close0 + 1,
            "low": close0 - 1,
            "close": [close0 + i for i in range(n)],
            "adj_close": [close0 + i for i in range(n)],
            "volume": [1000 * (i + 1) for i in range(n)],
            "source": "test",
        }
    )


def test_init_db_idempotent(engine):
    init_db(engine)
    init_db(engine)
    with engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                sa.text("SELECT hypertable_name FROM timescaledb_information.hypertables")
            )
        }
    assert {"prices_daily", "macro_series"} <= names


def test_upsert_round_trip(clean):
    df = _prices()
    assert upsert_df(df, "prices_daily", ["symbol", "date"], clean) == 3
    out = read_prices(SYMBOL, engine=clean)
    assert list(out.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert isinstance(out.index, pd.DatetimeIndex) and out.index.tz is None
    assert len(out) == 3
    assert out["close"].tolist() == df["close"].tolist()
    assert out["volume"].tolist() == df["volume"].tolist()

    # 동일 키 재삽입 -> 갱신, 행 수 불변
    df2 = _prices(close0=20.0)
    assert upsert_df(df2, "prices_daily", ["symbol", "date"], clean) == 3
    out2 = read_prices(SYMBOL, engine=clean)
    assert len(out2) == 3
    assert out2["close"].iloc[0] == 20.0

    # 기간 필터
    sub = read_prices(SYMBOL, start="2024-01-03", end="2024-01-03", engine=clean)
    assert len(sub) == 1


def test_upsert_chunking_and_nan(clean):
    df = _prices(n=12001)
    df.loc[5, "volume"] = None
    assert upsert_df(df, "prices_daily", ["symbol", "date"], clean) == 12001
    assert len(read_prices(SYMBOL, engine=clean)) == 12001


def test_macro_round_trip(clean):
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"series_id": SERIES, "date": dates, "value": [1.0, 2.0, 3.0], "source": "fred"}
    )
    assert upsert_df(df, "macro_series", ["series_id", "date"], clean) == 3
    s = read_macro(SERIES, engine=clean)
    assert s.tolist() == [1.0, 2.0, 3.0]
    assert isinstance(s.index, pd.DatetimeIndex)
