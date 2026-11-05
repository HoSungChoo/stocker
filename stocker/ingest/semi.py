"""산업 사이클 데이터: WSTS 월간 반도체 매출(시드 CSV)과 마이크론 분기 실적(yfinance)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "wsts_sales_world.csv"
WSTS_METRIC = "wsts_sales_world_usd_m"
MU_REVENUE_METRIC = "mu_revenue_usd_m"
MU_MARGIN_METRIC = "mu_gross_margin"

COLUMNS = ["metric", "date", "value", "source"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})


def load_wsts_seed(path: Path | str = SEED_PATH) -> pd.DataFrame:
    """seed CSV → long format (metric,date,value,source). `#` 주석 무시, 날짜는 월말로 정규화."""
    raw = pd.read_csv(path, comment="#", parse_dates=["date"])
    raw = raw.dropna(subset=["value"])
    date = pd.DatetimeIndex(raw["date"]).to_period("M").to_timestamp(how="end").normalize()
    df = pd.DataFrame(
        {
            "metric": WSTS_METRIC,
            "date": date,
            "value": raw["value"].astype("float64").to_numpy(),
            "source": "wsts",
        }
    )
    return df.sort_values("date").reset_index(drop=True)[COLUMNS]


def fetch_mu_financials() -> pd.DataFrame:
    """yfinance MU 분기 손익계산서 → mu_revenue_usd_m(백만 달러), mu_gross_margin(0~1).

    yfinance는 보통 최근 4~5분기만 준다. 날짜는 분기말. 못 받으면 경고 후 빈 DataFrame.
    """
    ticker = yf.Ticker("MU")
    needed = {"Total Revenue", "Gross Profit"}
    stmt = ticker.quarterly_financials
    if stmt is None or stmt.empty or not needed <= set(stmt.index):
        stmt = ticker.quarterly_income_stmt
    if stmt is None or stmt.empty or not needed <= set(stmt.index):
        logger.warning("MU 분기 재무제표를 받지 못했다")
        return _empty()

    rev = stmt.loc["Total Revenue"].astype("float64")
    gp = stmt.loc["Gross Profit"].astype("float64")
    ok = rev.notna() & gp.notna() & (rev > 0)
    rev, gp = rev[ok], gp[ok]
    idx = pd.DatetimeIndex(rev.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    idx = idx.normalize()

    revenue = pd.DataFrame(
        {
            "metric": MU_REVENUE_METRIC,
            "date": idx,
            "value": (rev / 1e6).to_numpy(),
            "source": "yfinance",
        }
    )
    margin = pd.DataFrame(
        {
            "metric": MU_MARGIN_METRIC,
            "date": idx,
            "value": (gp / rev).to_numpy(),
            "source": "yfinance",
        }
    )
    df = pd.concat([revenue, margin], ignore_index=True)
    return df.sort_values(["metric", "date"]).reset_index(drop=True)[COLUMNS]


def fetch_semi() -> pd.DataFrame:
    """WSTS 시드 + MU 실적을 하나의 long format DataFrame으로."""
    return pd.concat([load_wsts_seed(), fetch_mu_financials()], ignore_index=True)


def store_semi(df: pd.DataFrame, engine=None) -> int:
    """semi_cycle upsert. 영향 행 수 반환."""
    from stocker.db import upsert_df  # 지연 import

    return upsert_df(df, "semi_cycle", pk=["metric", "date"], engine=engine)


def ingest_semi(engine=None) -> int:
    """WSTS 시드와 MU 실적을 합쳐 semi_cycle에 upsert. 영향 행 수 반환."""
    return store_semi(fetch_semi(), engine=engine)
