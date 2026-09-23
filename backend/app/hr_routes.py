"""Register HR analytics without weakening the existing role dependency."""

from typing import Annotated

from fastapi import Depends, FastAPI, Query

from . import contracts as c
from .auth import require_hr
from .hr_analytics import build_hr_analytics
from .service import CareerService


def register_hr_routes(app: FastAPI, career: CareerService) -> None:
    @app.get(
        "/api/hr/analytics",
        response_model=c.HRAnalyticsResponse,
        responses={status: {"model": c.ErrorResponse} for status in (401, 403, 422, 503)},
        tags=["implemented"],
        openapi_extra={"x-implementation-status": "implemented"},
    )
    def hr_analytics(
        window_days: Annotated[int, Query(ge=1, le=365)] = 90,
        user=Depends(require_hr),
    ):
        return build_hr_analytics(career, window_days)
