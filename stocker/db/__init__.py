"""DB 접근은 이 패키지에만 둔다."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from stocker.config import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
CHUNK_ROWS = 5000
_MAX_PARAMS = 60000  # 드라이버 바인드 파라미터 한도(65535) 아래로 유지

_PRICE_COLS = ["open", "high", "low", "close", "adj_close", "volume"]


def get_engine() -> sa.Engine:
    return sa.create_engine(settings.db_url, pool_pre_ping=True)


def init_db(engine: sa.Engine | None = None) -> None:
    """schema.sql 실행 (idempotent)."""
    engine = engine or get_engine()
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [s.strip() for s in _strip_comments(sql).split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.exec_driver_sql(stmt)


def _strip_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


def _to_python(value, col_type):
    """pandas/numpy 값을 DB 드라이버가 받는 파이썬 값으로 변환."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date() if isinstance(col_type, sa.Date) else value.to_pydatetime()
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
        if isinstance(value, float) and value != value:
            return None
    if isinstance(col_type, sa.Integer) and isinstance(value, float):
        return int(value)
    return value


def upsert_df(df: pd.DataFrame, table: str, pk: list[str], engine: sa.Engine | None = None) -> int:
    """INSERT ... ON CONFLICT (pk) DO UPDATE. 반환: 영향 행 수."""
    if df.empty:
        return 0
    engine = engine or get_engine()
    df = df.reset_index() if any(c in (df.index.names or []) for c in pk) else df
    missing = [c for c in pk if c not in df.columns]
    if missing:
        raise ValueError(f"pk columns missing from DataFrame: {missing}")
    df = df.drop_duplicates(subset=pk, keep="last")

    tbl = sa.Table(table, sa.MetaData(), autoload_with=engine)
    unknown = [c for c in df.columns if c not in tbl.c]
    if unknown:
        raise ValueError(f"columns not in table {table}: {unknown}")

    cols = list(df.columns)
    update_cols = [c for c in cols if c not in pk]
    chunk = max(1, min(CHUNK_ROWS, _MAX_PARAMS // len(cols)))

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), chunk):
            part = df.iloc[i : i + chunk]
            rows = [
                {c: _to_python(v, tbl.c[c].type) for c, v in zip(cols, rec, strict=True)}
                for rec in part.astype(object).itertuples(index=False, name=None)
            ]
            stmt = pg_insert(tbl).values(rows)
            set_ = {c: stmt.excluded[c] for c in update_cols}
            if "ingested_at" in tbl.c and "ingested_at" not in cols:
                set_["ingested_at"] = sa.func.now()
            if set_:
                stmt = stmt.on_conflict_do_update(index_elements=pk, set_=set_)
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=pk)
            # 암시적 RETURNING 때문에 rowcount가 -1이 되므로 반환 행 수로 센다
            total += len(conn.execute(stmt.returning(tbl.c[pk[0]])).all())
    return total


def _date_bound(value) -> dt.date | None:
    return None if value is None else pd.Timestamp(value).date()


def read_prices(symbol: str, start=None, end=None, engine: sa.Engine | None = None) -> pd.DataFrame:
    """index=date(DatetimeIndex, tz-naive), columns=open..volume."""
    engine = engine or get_engine()
    sql = (
        "SELECT date, open, high, low, close, adj_close, volume FROM prices_daily "
        "WHERE symbol = :symbol AND (CAST(:start AS date) IS NULL OR date >= :start) "
        "AND (CAST(:end AS date) IS NULL OR date <= :end) ORDER BY date"
    )
    params = {"symbol": symbol, "start": _date_bound(start), "end": _date_bound(end)}
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params=params)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[_PRICE_COLS]


def read_macro(series_id: str, start=None, end=None, engine: sa.Engine | None = None) -> pd.Series:
    engine = engine or get_engine()
    sql = (
        "SELECT date, value FROM macro_series "
        "WHERE series_id = :sid AND (CAST(:start AS date) IS NULL OR date >= :start) "
        "AND (CAST(:end AS date) IS NULL OR date <= :end) ORDER BY date"
    )
    params = {"sid": series_id, "start": _date_bound(start), "end": _date_bound(end)}
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params=params)
    idx = pd.DatetimeIndex(pd.to_datetime(df["date"]), name="date")
    return pd.Series(df["value"].to_numpy(dtype="float64"), index=idx, name=series_id)


def read_cycle(metric: str, start=None, end=None, engine: sa.Engine | None = None) -> pd.Series:
    """산업 사이클 지표(semi_cycle). index=date(tz-naive), name=metric."""
    engine = engine or get_engine()
    sql = (
        "SELECT date, value FROM semi_cycle "
        "WHERE metric = :metric AND (CAST(:start AS date) IS NULL OR date >= :start) "
        "AND (CAST(:end AS date) IS NULL OR date <= :end) ORDER BY date"
    )
    params = {"metric": metric, "start": _date_bound(start), "end": _date_bound(end)}
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params=params)
    idx = pd.DatetimeIndex(pd.to_datetime(df["date"]), name="date")
    return pd.Series(df["value"].to_numpy(dtype="float64"), index=idx, name=metric)


def read_events(
    start=None,
    end=None,
    kinds: list[str] | None = None,
    engine: sa.Engine | None = None,
) -> pd.DataFrame:
    """이벤트(events). columns=date,kind,title,symbol,scheduled,source,meta. date 오름차순.

    date는 tz-naive datetime64, meta는 dict.
    """
    engine = engine or get_engine()
    sql = (
        "SELECT date, kind, title, symbol, scheduled, source, meta FROM events "
        "WHERE (CAST(:start AS date) IS NULL OR date >= :start) "
        "AND (CAST(:end AS date) IS NULL OR date <= :end) "
        "AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[]))) "
        "ORDER BY date, kind, symbol NULLS FIRST, title"
    )
    params = {"start": _date_bound(start), "end": _date_bound(end), "kinds": kinds}
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params=params)
    df["date"] = pd.to_datetime(df["date"])
    return df


def save_report_run(
    run_date,
    window: int,
    result: dict | None,
    report_md: str | None = None,
    engine: sa.Engine | None = None,
) -> None:
    """report_runs에 (run_date, window) 한 행을 upsert. 같은 날 다시 실행하면 덮어쓴다."""
    engine = engine or get_engine()
    sql = (
        'INSERT INTO report_runs (run_date, "window", result, report_md) '
        "VALUES (:run_date, :window, CAST(:result AS jsonb), :report_md) "
        'ON CONFLICT (run_date, "window") DO UPDATE SET '
        "created_at = now(), result = EXCLUDED.result, report_md = EXCLUDED.report_md"
    )
    params = {
        "run_date": _date_bound(run_date),
        "window": int(window),
        "result": None if result is None else json.dumps(result, ensure_ascii=False),
        "report_md": report_md,
    }
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def read_report_run(
    date=None,
    window: int = 0,
    before=None,
    engine: sa.Engine | None = None,
) -> dict | None:
    """report_runs에서 한 행을 dict로 읽는다. 없으면 None.

    date=None이면 해당 window의 가장 최근 run_date. before가 있으면 run_date < before 중
    가장 최근(전일 비교용). 반환 키: run_date(date), created_at, window, result(dict), report_md.
    """
    engine = engine or get_engine()
    sql = (
        'SELECT run_date, created_at, "window", result, report_md FROM report_runs '
        'WHERE "window" = :window AND (CAST(:date AS date) IS NULL OR run_date = :date) '
        "AND (CAST(:before AS date) IS NULL OR run_date < :before) "
        "ORDER BY run_date DESC LIMIT 1"
    )
    params = {"window": int(window), "date": _date_bound(date), "before": _date_bound(before)}
    with engine.connect() as conn:
        row = conn.execute(sa.text(sql), params).mappings().first()
    return None if row is None else dict(row)


def read_positions(start=None, end=None, engine: sa.Engine | None = None) -> pd.DataFrame:
    """일별 보유 종목 스냅샷(positions_snapshot). snapshot_date는 tz-naive datetime64.

    snapshot_date, symbol 오름차순. 없으면 빈 DataFrame(컬럼은 유지).
    """
    engine = engine or get_engine()
    sql = (
        "SELECT snapshot_date, symbol, name, market_country, currency, quantity, avg_price, "
        "last_price, market_value, pnl, pnl_rate, source, fetched_at FROM positions_snapshot "
        "WHERE (CAST(:start AS date) IS NULL OR snapshot_date >= :start) "
        "AND (CAST(:end AS date) IS NULL OR snapshot_date <= :end) "
        "ORDER BY snapshot_date, symbol"
    )
    params = {"start": _date_bound(start), "end": _date_bound(end)}
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params=params)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    return df


def read_broker_token(client_id: str, engine: sa.Engine | None = None) -> dict | None:
    """broker_tokens의 캐시된 토큰 한 행(access_token, expires_at, issued_at). 없으면 None."""
    engine = engine or get_engine()
    sql = (
        "SELECT access_token, expires_at, issued_at FROM broker_tokens WHERE client_id = :client_id"
    )
    with engine.connect() as conn:
        row = conn.execute(sa.text(sql), {"client_id": client_id}).mappings().first()
    return None if row is None else dict(row)


def write_broker_token(
    client_id: str,
    access_token: str,
    expires_at: dt.datetime,
    issued_at: dt.datetime | None = None,
    engine: sa.Engine | None = None,
) -> None:
    """broker_tokens에 클라이언트당 한 행을 upsert (새 토큰이 이전 토큰을 대체)."""
    engine = engine or get_engine()
    sql = (
        "INSERT INTO broker_tokens (client_id, access_token, expires_at, issued_at) "
        "VALUES (:client_id, :access_token, :expires_at, :issued_at) "
        "ON CONFLICT (client_id) DO UPDATE SET access_token = EXCLUDED.access_token, "
        "expires_at = EXCLUDED.expires_at, issued_at = EXCLUDED.issued_at"
    )
    params = {
        "client_id": client_id,
        "access_token": access_token,
        "expires_at": expires_at,
        "issued_at": issued_at or dt.datetime.now(dt.UTC),
    }
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def read_event_rows(
    kinds: list[str] | None = None,
    event_id: int | None = None,
    engine: sa.Engine | None = None,
) -> pd.DataFrame:
    """이벤트 원본 행(id,date,kind,title,source). event_impact 계산 입력용. date는 tz-naive."""
    engine = engine or get_engine()
    sql = (
        "SELECT id, date, kind, title, source FROM events "
        "WHERE (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[]))) "
        "AND (CAST(:event_id AS bigint) IS NULL OR id = :event_id) ORDER BY date, id"
    )
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params={"kinds": kinds, "event_id": event_id})
    df["date"] = pd.to_datetime(df["date"])
    return df


def read_event_impact(
    limit: int = 20,
    kinds: list[str] | None = None,
    engine: sa.Engine | None = None,
) -> pd.DataFrame:
    """이벤트 전후 수익률(event_impact)을 events와 조인해 |ret5| 내림차순으로.

    columns=id,date,kind,title,source,ret5,ret_1m,ret_3m. date는 tz-naive datetime64.
    ret5가 없는(None) 행은 맨 뒤. 계산된 적 없으면 빈 DataFrame(컬럼은 유지).
    """
    engine = engine or get_engine()
    sql = (
        "SELECT e.id, e.date, e.kind, e.title, e.source, i.ret5, i.ret_1m, i.ret_3m "
        "FROM event_impact i JOIN events e ON e.id = i.event_id "
        "WHERE (CAST(:kinds AS text[]) IS NULL OR e.kind = ANY(CAST(:kinds AS text[]))) "
        "ORDER BY abs(i.ret5) DESC NULLS LAST, e.date DESC, e.id LIMIT :limit"
    )
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params={"kinds": kinds, "limit": int(limit)})
    df["date"] = pd.to_datetime(df["date"])
    return df
