"""GET /api/similarity"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from stocker.api import data
from stocker.api.common import Common, iso, num
from stocker.api.schemas import ForwardSummaryRow, MatchOut, SimilarityOut
from stocker.similarity.engine import SimilarityResult

router = APIRouter(tags=["similarity"])


def result_out(res: SimilarityResult) -> SimilarityOut:
    cfg = res.config
    matches = [
        MatchOut(
            rank=i,
            start=iso(m.start_date),
            end=iso(m.end_date),
            scores={k: num(v) for k, v in m.scores.items()},
            combined=num(m.combined),
            forward={str(h): num(m.forward.get(h)) for h in cfg.horizons},
        )
        for i, m in enumerate(res.matches, 1)
    ]
    summary = [
        ForwardSummaryRow(
            horizon=int(h),
            median=num(r["median"]),
            p10=num(r["p10"]),
            p90=num(r["p90"]),
            n_up=int(r["n_up"]),
            n_down=int(r["n_down"]),
            n=int(r["n"]),
        )
        for h, r in res.forward_summary.iterrows()
    ]
    return SimilarityOut(
        as_of=iso(res.as_of),
        window=cfg.window,
        top_k=cfg.top_k,
        n_candidates=int(res.n_candidates),
        layers_missing=list(res.layers_missing),
        weights_used={k: float(v) for k, v in res.weights_used.items()},
        matches=matches,
        forward_summary=summary,
    )


def run_or_400(q) -> SimilarityResult:
    try:
        return data.similarity(q)
    except ValueError as exc:  # 데이터 부족, 후보 없음 등
        raise HTTPException(400, str(exc)) from exc


@router.get("/similarity", response_model=SimilarityOut)
def similarity(q: Common) -> SimilarityOut:
    return result_out(run_or_400(q))
