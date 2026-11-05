"""React 프론트용 읽기 전용 HTTP API (FastAPI). 계약: docs/PHASE5_CONTRACT.md 17절.

계산은 복제하지 않는다: stocker.similarity(score/explain), stocker.db, stocker.sim,
stocker.broker.positions를 호출만 하고 JSON으로 바꾼다.
"""

from stocker.api.app import app, create_app

__all__ = ["app", "create_app"]
