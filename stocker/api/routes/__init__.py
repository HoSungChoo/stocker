"""엔드포인트 모음. 각 모듈이 APIRouter `router`를 가진다."""

from stocker.api.routes import events, explain, health, overlay, positions, report, similarity

ROUTERS = [
    health.router,
    similarity.router,
    overlay.router,
    explain.router,
    events.router,
    positions.router,
    report.router,
]

__all__ = ["ROUTERS"]
