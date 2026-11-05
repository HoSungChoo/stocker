"""GET /api/report/latest — report_runs의 최신 전체 보고서(window=0) Markdown. 없으면 null."""

from __future__ import annotations

from fastapi import APIRouter

from stocker.api import data
from stocker.api.common import iso
from stocker.api.schemas import ReportLatest

router = APIRouter(prefix="/report", tags=["report"])


@router.get("/latest", response_model=ReportLatest | None)
def latest() -> ReportLatest | None:
    row = data.report_run(window=0)
    if row is None:
        return None
    return ReportLatest(run_date=iso(row["run_date"]), markdown=row.get("report_md"))
