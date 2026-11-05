"""GET /api/health"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from stocker.api import data
from stocker.api.common import iso
from stocker.api.schemas import Health

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health() -> Health:
    """DB가 닿고 시세가 있으면 ok. 아니면 ok=false와 오류 종류(200으로 돌려 배너에 쓴다)."""
    try:
        return Health(
            ok=True,
            last_trading_day=iso(data.last_trading_day()),
            layers_missing=data.layers_missing_static(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("health 실패: %s", exc)
        return Health(
            ok=False, last_trading_day=None, layers_missing=[], error=exc.__class__.__name__
        )
