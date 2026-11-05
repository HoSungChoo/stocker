"""GET /api/overlay — 현재 창과 과거 창을 '창 시작=100', d='창 시작 후 거래일'로 맞춘 경로.

경로 계산은 Streamlit 대시보드와 같은 `stocker.web.charts.build_overlay`를 쓴다(과거 창은
창 끝 이후 252거래일까지). 이벤트는 각 경로 위에 `attach_events`로 올린다(휴장일 이벤트는 다음
거래일). 과거 국면의 이벤트는 점선 구간(after)까지 포함한다. 프론트는 d < window로 창 안만
거를 수 있다.
"""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter

from stocker.api import data
from stocker.api.common import Common, iso
from stocker.api.routes.similarity import run_or_400
from stocker.api.schemas import CurrentPath, EventMarker, MatchPath, OverlayOut, Point
from stocker.web.charts import EXTEND_DAYS, attach_events, build_overlay

router = APIRouter(tags=["overlay"])


def _points(g: pd.DataFrame) -> list[Point]:
    return [Point(d=int(d), v=float(v)) for d, v in zip(g["day"], g["level"], strict=True)]


def _markers(ev: pd.DataFrame, label: str) -> list[EventMarker]:
    if ev.empty:
        return []
    g = ev[ev["label"] == label]
    return [
        EventMarker(d=int(r.day), date=iso(r.date), kind=str(r.kind), title=str(r.title))
        for r in g.itertuples(index=False)
    ]


@router.get("/overlay", response_model=OverlayOut)
def overlay(q: Common) -> OverlayOut:
    res = run_or_400(q)
    window = res.config.window
    close = data.close_series()
    paths = build_overlay(close, res.matches, res.as_of, window, extend=EXTEND_DAYS)
    ev = data.events(paths["date"].min(), paths["date"].max())
    marks = attach_events(paths, ev)

    cur = paths[paths["role"] == "current"]
    current = CurrentPath(
        start=iso(cur["date"].iloc[0]),
        end=iso(cur["date"].iloc[-1]),
        points=_points(cur),
        events=_markers(marks, "현재"),
    )
    past_labels = list(paths.loc[paths["role"] == "past", "label"].unique())
    matches = []
    for i, (m, label) in enumerate(zip(res.matches, past_labels, strict=True), 1):
        g = paths[paths["label"] == label]
        matches.append(
            MatchPath(
                rank=i,
                start=iso(m.start_date),
                end=iso(m.end_date),
                window_points=_points(g[g["day"] < window]),
                after_points=_points(g[g["day"] >= window]),
                events=_markers(marks, label),
            )
        )
    return OverlayOut(current=current, matches=matches)
