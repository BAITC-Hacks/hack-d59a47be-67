import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from . import contracts as c
from .ai_adapter import AIAdapter
from .auth import COOKIE, digest, login, require_employee, require_hr, require_user
from .config import Settings
from .database import Database, MigrationError
from .errors import APIError

log = logging.getLogger("career_quest")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database = Database(config)
    adapter = AIAdapter(config.ai_enabled)

    @asynccontextmanager
    async def lifespan(app):
        try:
            database.migrate()
        except (sqlite3.Error, OSError, MigrationError):
            log.error("Database initialization failed; readiness is unavailable")
        yield

    app = FastAPI(
        title="Career Quest API",
        version="1.0.0",
        lifespan=lifespan,
        description="Implemented: health, readiness, version and demo auth. "
        "Domain routes are planned: authenticated requests return 501. "
        "Preview is a team addition. Dataset importer and AI core are not implemented.",
    )
    app.state.settings, app.state.database, app.state.ai_adapter = config, database, adapter
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )

    def error(request, status, code, message, details=None):
        return JSONResponse(
            status_code=status,
            content={
                "code": code,
                "message": message,
                "request_id": request.state.request_id,
                "details": details or {},
            },
        )

    @app.middleware("http")
    async def request_boundary(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        try:
            response = await call_next(request)
        except (sqlite3.Error, OSError):
            response = error(request, 503, "STORAGE_UNAVAILABLE", "Storage is temporarily unavailable.")
        except Exception:
            # Do not log request bodies, credentials, raw profiles or exception text.
            log.error("Unhandled request failure request_id=%s", request.state.request_id)
            response = error(request, 500, "INTERNAL_ERROR", "An internal error occurred.")
        if response.status_code >= 400 and not response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            response = error(request, response.status_code, "HTTP_ERROR", "Request could not be completed.")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Boundary-generated failures must remain readable by the allowed frontend.
        origin = request.headers.get("origin")
        if origin in config.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
            vary = response.headers.get("Vary", "")
            if "origin" not in {value.strip().lower() for value in vary.split(",")}:
                response.headers["Vary"] = f"{vary}, Origin" if vary else "Origin"
        return response

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        response = error(request, exc.status, exc.code, exc.message, exc.details)
        if exc.status == 429:
            response.headers["Retry-After"] = "900"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's input/ctx may contain passwords or raw imported data.
        fields = [{"location": list(item["loc"]), "type": item["type"]} for item in exc.errors()]
        return error(request, 422, "VALIDATION_ERROR", "Request validation failed.", {"fields": fields})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        codes = {
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            503: "UNAVAILABLE",
        }
        return error(
            request,
            exc.status_code,
            codes.get(exc.status_code, "HTTP_ERROR"),
            "Route not found." if exc.status_code == 404 else "Request could not be completed.",
        )

    def capabilities():
        return {"ai": adapter.capability, "dataset": {"status": "not_loaded", "version": None}}

    implemented = {"x-implementation-status": "implemented"}

    @app.get("/api/health", response_model=c.HealthResponse, tags=["implemented"], openapi_extra=implemented)
    def health():
        return {"status": "ok", "capabilities": capabilities()}

    @app.get(
        "/api/health/ready",
        response_model=c.ReadinessResponse,
        responses={503: {"model": c.ErrorResponse}},
        tags=["implemented"],
        openapi_extra=implemented,
    )
    def ready():
        if not database.readiness():
            raise APIError(
                503,
                "NOT_READY",
                "Database or migrations are not ready.",
                {"database": "unavailable", "capabilities": capabilities()},
            )
        return {"status": "ready", "database": "ready", "capabilities": capabilities()}

    @app.get(
        "/api/version", response_model=c.VersionResponse, tags=["implemented"], openapi_extra=implemented
    )
    def version():
        return {"api_version": "1.0.0", "commit_sha": config.commit_sha.lower()}

    auth_errors = {status: {"model": c.ErrorResponse} for status in [401, 403, 422, 429, 503]}

    @app.post(
        "/api/auth/login",
        response_model=c.SessionResponse,
        responses=auth_errors,
        tags=["implemented"],
        openapi_extra=implemented,
    )
    def sign_in(payload: c.LoginRequest, request: Request, response: Response):
        result, token = login(request, payload.username, payload.password.get_secret_value())
        response.set_cookie(
            COOKIE,
            token,
            max_age=config.session_ttl_seconds,
            httponly=True,
            secure=config.cookie_secure,
            samesite="lax",
            path="/api",
        )
        return result

    @app.post(
        "/api/auth/logout",
        response_model=c.LogoutResponse,
        responses=auth_errors,
        tags=["implemented"],
        openapi_extra=implemented,
    )
    def sign_out(request: Request, response: Response, user=Depends(require_user)):
        with database.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest(request.cookies[COOKIE]),))
        response.delete_cookie(
            COOKIE, path="/api", secure=config.cookie_secure, httponly=True, samesite="lax"
        )
        return {"status": "logged_out"}

    @app.get(
        "/api/me",
        response_model=c.UserIdentity,
        responses=auth_errors,
        tags=["implemented"],
        openapi_extra=implemented,
    )
    def me(user=Depends(require_user)):
        return user

    def planned():
        raise APIError(
            501,
            "NOT_IMPLEMENTED",
            "This domain endpoint is planned for a later step.",
            {"capabilities": capabilities()},
        )

    domain_errors = {status: {"model": c.ErrorResponse} for status in [401, 403, 404, 409, 422, 501, 503]}
    planned_options = dict(
        status_code=501,
        response_model=c.ErrorResponse,
        responses=domain_errors,
        tags=["planned"],
        openapi_extra={"x-implementation-status": "planned"},
        description="Planned v1 domain operation. Access checks are implemented; domain logic returns 501.",
    )

    @app.get("/api/catalog", **planned_options)
    def catalog(user=Depends(require_user)):
        planned()

    @app.get("/api/employees", **planned_options)
    def employees(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        user=Depends(require_hr),
    ):
        planned()

    @app.get("/api/employees/{employee_id}", **planned_options)
    def employee(user=Depends(require_employee)):
        planned()

    @app.patch("/api/employees/{employee_id}/goal", **planned_options)
    def goal(payload: c.GoalUpdateRequest, user=Depends(require_employee)):
        planned()

    @app.post("/api/employees/{employee_id}/recommendations", **planned_options)
    def recommendations(payload: c.RecommendationRequest, user=Depends(require_employee)):
        planned()

    @app.post("/api/employees/{employee_id}/preview", **planned_options)
    def preview(payload: c.PreviewRequest, user=Depends(require_employee)):
        planned()

    @app.post("/api/employees/{employee_id}/completions", **planned_options)
    def completions(payload: c.CompletionRequest, user=Depends(require_employee)):
        planned()

    @app.get("/api/hr/summary", **planned_options)
    def summary(user=Depends(require_hr)):
        planned()

    @app.post("/api/hr/import", **planned_options)
    def import_data(payload: c.ImportRequest, user=Depends(require_hr)):
        planned()

    # A single contract module defines both implemented and future HTTP schemas.
    original_openapi = app.openapi

    def openapi():
        document = original_openapi()
        document["components"]["securitySchemes"] = {
            "DemoSession": {"type": "apiKey", "in": "cookie", "name": COOKIE},
            "CsrfToken": {
                "type": "apiKey",
                "in": "header",
                "name": "X-CSRF-Token",
                "description": "Use csrf_token from login for every authenticated mutation.",
            },
        }
        for path, methods in document["paths"].items():
            for method, operation in methods.items():
                if method in {"get", "post", "patch"} and (
                    operation.get("x-implementation-status") == "planned"
                    or path in {"/api/me", "/api/auth/logout"}
                ):
                    operation["security"] = [{"DemoSession": []}]
                    if method in {"post", "patch"}:
                        operation["security"][0]["CsrfToken"] = []
                if method in {"post", "patch"} and not any(
                    parameter.get("name") == "Origin" for parameter in operation.get("parameters", [])
                ):
                    operation.setdefault("parameters", []).append(
                        {
                            "name": "Origin",
                            "in": "header",
                            "required": True,
                            "description": "Exact allowlisted origin; browsers send this automatically.",
                            "schema": {"type": "string"},
                        }
                    )
        return document

    app.openapi = openapi
    return app


app = create_app()
