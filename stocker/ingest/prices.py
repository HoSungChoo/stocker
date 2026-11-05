"""yfinance 시세 수집. long format DataFrame을 만들고 prices_daily에 upsert."""

from __future__ import annotations

import sys

import pandas as pd
import yfinance as yf

DEFAULT_SYMBOLS = ["^SOX", "SOXX", "SOXL", "SMH"]

COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
_FIELD_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}
_PRICE_COLS = ["open", "high", "low", "close", "adj_close"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})


def _symbol_frame(raw: pd.DataFrame, symbol: str, n_symbols: int) -> pd.DataFrame | None:
    """다운로드 결과에서 한 심볼의 필드 컬럼 DataFrame을 꺼낸다. 없으면 None."""
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        if symbol in cols.get_level_values(0):  # group_by="ticker": (ticker, field)
            return raw[symbol]
        if symbol in cols.get_level_values(1):  # (field, ticker)
            return raw.xs(symbol, axis=1, level=1)
        return None
    # 평평한 컬럼은 심볼이 하나일 때만 가능
    return raw if n_symbols == 1 else None


def _normalize(sub: pd.DataFrame, symbol: str) -> pd.DataFrame:
    sub = sub.rename(columns=_FIELD_MAP)
    missing = [c for c in [*_PRICE_COLS, "volume"] if c not in sub.columns]
    if missing:
        raise ValueError(f"{symbol}: yfinance 응답에 컬럼이 없다: {missing}")
    sub = sub[[*_PRICE_COLS, "volume"]].dropna()
    idx = pd.DatetimeIndex(sub.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out = sub.astype({c: "float64" for c in _PRICE_COLS})
    out["volume"] = sub["volume"].astype("int64")
    out.insert(0, "date", idx.normalize())
    out.insert(0, "symbol", symbol)
    out["source"] = "yfinance"
    return out.reset_index(drop=True)[COLUMNS]


def fetch_prices(symbols: list[str], start="1990-01-01", end=None) -> pd.DataFrame:
    """long format: symbol,date,open,high,low,close,adj_close,volume,source.

    auto_adjust=False. 결측 행 제거. date는 tz-naive. end는 포함(inclusive).
    데이터를 못 받은 심볼은 경고만 하고 건너뛴다.
    """
    symbols = list(symbols)
    if not symbols:
        return _empty()
    # yfinance의 end는 배타적이므로 하루 더한다.
    end_excl = None if end is None else pd.Timestamp(end) + pd.Timedelta(days=1)
    raw = yf.download(
        symbols,
        start=start,
        end=end_excl,
        auto_adjust=False,
        group_by="ticker",
        threads=False,
        progress=False,
    )
    frames = []
    for sym in symbols:
        sub = None if raw is None or raw.empty else _symbol_frame(raw, sym, len(symbols))
        norm = None if sub is None else _normalize(sub, sym)
        if norm is None or norm.empty:
            print(f"[warn] {sym}: 받은 데이터가 없다", file=sys.stderr)
            continue
        frames.append(norm)
    if not frames:
        return _empty()
    return pd.concat(frames, ignore_index=True)


def store_prices(df: pd.DataFrame, engine=None) -> int:
    """fetch_prices 결과를 prices_daily에 upsert. 영향 행 수 반환."""
    from stocker.db import upsert_df  # 지연 import

    return upsert_df(df, "prices_daily", pk=["symbol", "date"], engine=engine)


def ingest_prices(symbols=DEFAULT_SYMBOLS, start="1990-01-01", engine=None) -> int:
    return store_prices(fetch_prices(list(symbols), start=start), engine=engine)
