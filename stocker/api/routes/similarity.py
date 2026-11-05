"""GET /api/similarity"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from stocker.api import data
from stocker.api.common import Common, iso, jsonable, num
from stocker.api.schemas import ForwardSummaryRow, MatchOut, SimilarityOut
from stocker.similarity.engine import SimilarityResult

router = APIRouter(tags=["similarity"])


def _summary_rows(df) -> list[ForwardSummaryRow]:
    return [
        ForwardSummaryRow(
            horizon=int(h),
            median=num(r["median"]),
            p10=num(r["p10"]),
            p90=num(r["p90"]),
            n_up=int(r["n_up"]),
            n_down=int(r["n_down"]),
            n=int(r["n"]),
        )
        for h, r in df.iterrows()
    ]


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
    summary = _summary_rows(res.forward_summary)
    baseline_df = getattr(res, "forward_summary_baseline", None)
    fit = getattr(res, "fit", None)  # 세션 R의 적합 판정(23절). 아직 없으면 전부 null
    applicable_n = getattr(res, "applicable_n", None)
    return SimilarityOut(
        as_of=iso(res.as_of),
        window=cfg.window,
        top_k=cfg.top_k,
        n_candidates=int(res.n_candidates),
        layers_missing=list(res.layers_missing),
        weights_used={k: float(v) for k, v in res.weights_used.items()},
        matches=matches,
        forward_summary=summary,
        forward_summary_baseline=None if baseline_df is None else _summary_rows(baseline_df),
        mode=getattr(cfg, "mode", None),
        fit=None if fit is None else jsonable(list(fit)),
        applicable=getattr(res, "applicable", None),
        applicable_n=None if applicable_n is None else int(applicable_n),
        forward_summary_source=getattr(res, "forward_summary_source", None),
    )


def run_or_400(q) -> SimilarityResult:
    try:
        return data.similarity(q)
    except ValueError as exc:  # 데이터 부족, 후보 없음 등
        raise HTTPException(400, str(exc)) from exc


@router.get("/similarity", response_model=SimilarityOut)
def similarity(q: Common) -> SimilarityOut:
    return result_out(run_or_400(q))
