"""보유 종목 스냅샷과 '유사 국면 분포를 내 평가액에 적용'. 계약: docs/DATA_CONTRACT.md 15절."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from stocker.db import read_positions, upsert_df

SNAPSHOT_COLUMNS = [
    "snapshot_date",
    "symbol",
    "name",
    "market_country",
    "currency",
    "quantity",
    "avg_price",
    "last_price",
    "market_value",
    "pnl",
    "pnl_rate",
    "source",
    "fetched_at",
]
SNAPSHOT_PK = ["snapshot_date", "symbol"]
SOXL_SYMBOL = "SOXL"
LATEST_MAX_AGE_DAYS = 4  # 이보다 오래된 스냅샷은 '지금 보유'로 보지 않는다 (주말·연휴 여유)

DISCLAIMER = "표본 n개의 과거 경로를 적용한 값이며 예측이 아니다."
BASIS_SOX = "SOX 기준"
BASIS_LEVERAGED = "3배 레버리지 기준"
DISTRIBUTION_COLUMNS = [
    "basis",
    "horizon",
    "n",
    "median",
    "p10",
    "p90",
    "value_median",
    "value_p10",
    "value_p90",
]


def _f(x) -> float | None:
    """API가 숫자 또는 숫자 문자열로 줄 수 있어 둘 다 받는다. 없으면 None."""
    if x is None or x == "":
        return None
    return float(x)


def holdings_frame(
    holdings: Mapping, snapshot_date: dt.date, fetched_at: dt.datetime
) -> pd.DataFrame:
    """holdings 응답(result dict) → positions_snapshot 컬럼의 DataFrame."""
    rows = []
    for it in holdings.get("items") or []:
        mv = it.get("marketValue") or {}
        pl = it.get("profitLoss") or {}
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "symbol": it["symbol"],
                "name": it.get("name"),
                "market_country": it.get("marketCountry"),
                "currency": it.get("currency"),
                "quantity": _f(it.get("quantity")),
                "avg_price": _f(it.get("averagePurchasePrice")),
                "last_price": _f(it.get("lastPrice")),
                "market_value": _f(mv.get("amount")),
                "pnl": _f(pl.get("amount")),
                "pnl_rate": _f(pl.get("rate")),  # 소수 (0.1077 = 10.77%)
                "source": "toss",
                "fetched_at": fetched_at,
            }
        )
    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)


def snapshot_positions(client, snapshot_date=None, engine=None) -> pd.DataFrame:
    """holdings를 받아 positions_snapshot에 upsert하고 그 DataFrame을 돌려준다.

    보유 종목이 없으면 아무것도 쓰지 않고 빈 DataFrame.
    """
    day = pd.Timestamp(snapshot_date).date() if snapshot_date else dt.date.today()
    df = holdings_frame(client.holdings(), day, dt.datetime.now(dt.UTC))
    if not df.empty:
        upsert_df(df, "positions_snapshot", SNAPSHOT_PK, engine=engine)
    return df


def latest_positions(engine=None, max_age_days: int | None = LATEST_MAX_AGE_DAYS) -> pd.DataFrame:
    """가장 최근 snapshot_date의 행들. 스냅샷이 없거나 max_age_days보다 오래됐으면 빈 DataFrame."""
    start = None if max_age_days is None else dt.date.today() - dt.timedelta(days=max_age_days)
    df = read_positions(start=start, engine=engine)
    if df.empty:
        return df
    return df[df["snapshot_date"] == df["snapshot_date"].max()].reset_index(drop=True)


def soxl_positions(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """보유 중인 SOXL 행만 (수량이 0이거나 없으면 None). 보고서·대시보드가 쓰는 대상."""
    if df is None or df.empty:
        return None
    rows = df[(df["symbol"] == SOXL_SYMBOL) & (df["quantity"].fillna(0) > 0)]
    return None if rows.empty else rows.reset_index(drop=True)


def leveraged_returns(
    close: pd.Series,
    matches: Sequence,
    window: int,
    rf: pd.Series | None = None,
    horizons: Sequence[int] = (21, 63, 126, 252),
) -> dict[int, list[float]]:
    """유사 국면마다 SOX 경로를 3배 레버리지(SOXL)로 따라간 horizon별 수익률 목록.

    경로는 대시보드 'SOXL 시뮬'과 같은 `charts.build_sim`이 만든 것이다(창 끝 이후 252거래일,
    같은 가정). 창 끝 날짜를 0으로 두고 h거래일 뒤 NAV를 센다. 미래가 모자란 국면은 뺀다.
    """
    from stocker.web import charts  # plotly만 필요하고 streamlit은 불러오지 않는다

    out: dict[int, list[float]] = {int(h): [] for h in horizons}
    if not matches:
        return out
    paths, _ = charts.build_sim(close, matches, window, rf=rf)
    for _, g in paths.groupby("label", sort=False):
        nav = g["sim"].to_numpy()
        if len(nav) < window or not nav[window - 1] > 0:
            continue
        base = nav[window - 1]
        for h in out:
            if window - 1 + h < len(nav):
                out[h].append(float(nav[window - 1 + h] / base - 1))
    return out


def _dist_row(basis, h, n, median, p10, p90, value) -> dict:
    return {
        "basis": basis,
        "horizon": int(h),
        "n": int(n),
        "median": median,
        "p10": p10,
        "p90": p90,
        "value_median": value * (1 + median),
        "value_p10": value * (1 + p10),
        "value_p90": value * (1 + p90),
    }


def apply_distribution(
    position_value: float,
    forward_summary: pd.DataFrame,
    leverage_paths: Mapping[int, Sequence[float]] | None,
) -> pd.DataFrame:
    """유사 국면의 이후 수익 분포(중앙값·하위10%·상위10%)를 평가액에 곱해 horizon별 금액으로.

    - "SOX 기준": forward_summary(SOX 지수 수익률)를 그대로 곱한다.
    - "3배 레버리지 기준": leverage_paths[h]는 국면별 3배 NAV 수익률 목록
      (`leveraged_returns`). SOX 분포를 3배 하지 않고, 경로를 일별로 3배 시뮬한 결과의
      분포를 쓴다(레버리지 감가가 들어간다). None이면 이 줄은 만들지 않는다.
    수익률이 없는 horizon(표본 0)은 건너뛴다. 반환 컬럼은 DISTRIBUTION_COLUMNS.
    """
    rows = []
    for h, r in forward_summary.iterrows():
        if pd.notna(r["median"]):
            rows.append(
                _dist_row(BASIS_SOX, h, r["n"], r["median"], r["p10"], r["p90"], position_value)
            )
    if leverage_paths:
        for h in forward_summary.index:
            vals = np.asarray(leverage_paths.get(int(h), []), dtype=float)
            vals = vals[~np.isnan(vals)]
            if len(vals) == 0:
                continue
            rows.append(
                _dist_row(
                    BASIS_LEVERAGED,
                    h,
                    len(vals),
                    float(np.median(vals)),
                    float(np.percentile(vals, 10)),
                    float(np.percentile(vals, 90)),
                    position_value,
                )
            )
    out = pd.DataFrame(rows, columns=DISTRIBUTION_COLUMNS)
    out["_order"] = out["basis"].map({BASIS_SOX: 0, BASIS_LEVERAGED: 1})
    return (
        out.sort_values(["horizon", "_order"], kind="stable")
        .drop(columns="_order")
        .reset_index(drop=True)
    )


def money(x, currency: str | None = "USD") -> str:
    """토스가 준 통화 그대로 표기: USD는 $1,234.56, KRW는 1,234,567원, 그 밖은 '1,234.56 EUR'."""
    if x is None or pd.isna(x):
        return "—"
    cur = (currency or "").upper()
    if cur == "USD":
        return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"
    if cur == "KRW":
        return f"{x:,.0f}원"
    return f"{x:,.2f} {cur}".strip()
