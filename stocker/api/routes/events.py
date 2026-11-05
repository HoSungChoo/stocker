"""GET /api/events/upcoming, /api/events/impact, /api/events/{id}/path

impact·path는 세션 B의 `stocker.events`·`stocker.db.read_event_impact`를 함수 안에서 lazy import
한다. 없으면 impact는 빈 목록, path는 404.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from stocker.api import data
from stocker.api.common import iso, num
from stocker.api.schemas import EventImpactRow, EventPathOut, EventRef, Point, UpcomingEvent

router = APIRouter(prefix="/events", tags=["events"])


def _str_or_none(x) -> str | None:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return str(x)


def _confirmed(meta, scheduled: bool) -> bool:
    """meta.confirmed가 있으면 그 값, 없으면 scheduled와 같게 본다 (events에 별도 컬럼이 없다)."""
    if isinstance(meta, dict) and "confirmed" in meta:
        return bool(meta["confirmed"])
    return scheduled


@router.get("/upcoming", response_model=list[UpcomingEvent])
def upcoming(days: Annotated[int, Query(ge=1, le=365)] = 14) -> list[UpcomingEvent]:
    start = data.today()
    df = data.events(start, start + dt.timedelta(days=days))
    out = []
    for r in df.itertuples(index=False):
        scheduled = (
            bool(r.scheduled) if r.scheduled is not None and r.scheduled == r.scheduled else False
        )
        out.append(
            UpcomingEvent(
                date=iso(r.date),
                kind=str(r.kind),
                title=str(r.title),
                symbol=_str_or_none(r.symbol),
                scheduled=scheduled,
                confirmed=_confirmed(getattr(r, "meta", None), scheduled),
            )
        )
    return out


@router.get("/impact", response_model=list[EventImpactRow])
def impact(
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
    kinds: Annotated[str | None, Query(description="쉼표 구분 kind 목록")] = None,
) -> list[EventImpactRow]:
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    df = data.event_impact(limit, kind_list)
    if df.empty:
        return []
    return [
        EventImpactRow(
            id=int(r["id"]),
            date=iso(r["date"]),
            kind=str(r["kind"]),
            title=str(r["title"]),
            ret5=num(r.get("ret5")),
            ret_1m=num(r.get("ret_1m")),
            ret_3m=num(r.get("ret_3m")),
            source=_str_or_none(r.get("source")),
        )
        for r in df.to_dict("records")
    ]


@router.get("/{event_id}/path", response_model=EventPathOut)
def path(event_id: int, days: Annotated[int, Query(ge=1, le=250)] = 30) -> EventPathOut:
    ev = data.event_by_id(event_id)
    if ev is None:
        raise HTTPException(
            404, f"이벤트 {event_id}를 찾을 수 없다 (event_impact가 아직 없을 수 있다)"
        )
    s = data.event_path(event_id, days)
    if s is None or len(s) == 0:
        raise HTTPException(404, f"이벤트 {event_id}의 경로를 만들 수 없다")
    points = [Point(d=int(d), v=float(v)) for d, v in s.items() if num(v) is not None]
    return EventPathOut(
        event=EventRef(
            id=ev["id"], date=iso(ev["date"]), kind=str(ev["kind"]), title=str(ev["title"])
        ),
        points=points,
    )
