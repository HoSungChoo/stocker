"""GET /api/explain?end=YYYY-MM-DD (+ 공통 쿼리)"""

from __future__ import annotations

from typing import Annotated

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from stocker.api import data
from stocker.api.common import Common
from stocker.api.schemas import ExplainOut
from stocker.similarity.comment import build_comment

router = APIRouter(tags=["explain"])


@router.get("/explain", response_model=ExplainOut)
def explain_route(
    q: Common, end: Annotated[str, Query(description="과거 국면 창의 끝 날짜 YYYY-MM-DD")]
) -> ExplainOut:
    try:
        end_norm = pd.Timestamp(end).strftime("%Y-%m-%d")
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"end 날짜 형식 오류: {end}") from exc
    try:
        exp = data.explanation(q, end_norm)
    except ValueError as exc:
        # 후보가 아닌 날짜(as_of와 너무 가깝거나 시세 이전)는 404, 데이터 부족은 400
        msg = str(exc)
        raise HTTPException(404 if "match_end" in msg else 400, msg) from exc
    body = exp.to_dict()
    fit = data.fit_for(q, exp.match.end)
    if fit is not None:  # 적합 판정이 있으면 판정 문장이 status를 따른다 (26절)
        body["comment"] = build_comment(exp, fit)
    return ExplainOut.model_validate(body)
