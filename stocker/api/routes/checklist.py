"""GET /api/checklist — 지금과 상위 K 국면(창 끝 날짜)의 체크리스트 신호 (계약 PHASE6C 30절).

점수와 무관하다. 각 날짜 d에서 d 이하 데이터만으로 신호를 평가한다.
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter

from stocker.api import data
from stocker.api.common import Common, iso, num
from stocker.api.routes.similarity import run_or_400
from stocker.api.schemas import ChecklistMatchOut, ChecklistOut, SignalOut

router = APIRouter(tags=["checklist"])


def _signals(date) -> list[SignalOut]:
    out = []
    for s in data.checklist_signals(date):
        d = dataclasses.asdict(s)
        d["value"] = num(d["value"])
        out.append(SignalOut(**d))
    return out


@router.get("/checklist", response_model=ChecklistOut)
def checklist(q: Common) -> ChecklistOut:
    res = run_or_400(q)
    return ChecklistOut(
        now=_signals(res.as_of),
        matches=[
            ChecklistMatchOut(rank=i, end=iso(m.end_date), signals=_signals(m.end_date))
            for i, m in enumerate(res.matches, 1)
        ],
    )
