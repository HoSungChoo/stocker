"""GET /api/positions/latest, /api/positions/apply — stocker.broker.positions를 호출만 한다."""

from __future__ import annotations

from fastapi import APIRouter

from stocker.api import data
from stocker.api.common import Common, iso, num
from stocker.api.routes.similarity import run_or_400
from stocker.api.schemas import ApplyOut, ApplyRow, PositionItem, PositionsLatest
from stocker.broker.positions import BASIS_LEVERAGED, BASIS_SOX, soxl_positions

router = APIRouter(prefix="/positions", tags=["positions"])

BASIS_KEY = {BASIS_SOX: "sox", BASIS_LEVERAGED: "lev3"}


def _str(x) -> str | None:
    return None if x is None or (isinstance(x, float) and x != x) else str(x)


@router.get("/latest", response_model=PositionsLatest | None)
def latest() -> PositionsLatest | None:
    df = data.latest_positions()
    if df is None:
        return None
    items = [
        PositionItem(
            symbol=str(r.symbol),
            quantity=num(r.quantity),
            avg_price=num(r.avg_price),
            last_price=num(r.last_price),
            market_value=num(r.market_value),
            pnl=num(r.pnl),
            pnl_rate=num(r.pnl_rate),
            currency=_str(r.currency),
        )
        for r in df.itertuples(index=False)
    ]
    return PositionsLatest(snapshot_date=iso(df["snapshot_date"].max()), items=items)


@router.get("/apply", response_model=ApplyOut | None)
def apply(q: Common) -> ApplyOut | None:
    """보유 SOXL 평가액에 유사 국면의 이후 수익 분포를 적용. 보유가 없으면 null."""
    held = soxl_positions(data.latest_positions())
    if held is None:
        return None
    value = float(held["market_value"].fillna(0).sum())
    currency = _str(held["currency"].iloc[0])
    res = run_or_400(q)
    lev = data.leveraged_returns(
        data.close_series(), res.matches, res.config.window, data.rf_series()
    )
    dist = data.apply_distribution(value, res.forward_summary, lev)
    rows = [
        ApplyRow(
            basis=BASIS_KEY.get(r["basis"], str(r["basis"])),
            horizon=int(r["horizon"]),
            median=num(r["median"]),
            p10=num(r["p10"]),
            p90=num(r["p90"]),
        )
        for r in dist.to_dict("records")
    ]
    return ApplyOut(position_value=value, currency=currency, n=len(res.matches), rows=rows)
