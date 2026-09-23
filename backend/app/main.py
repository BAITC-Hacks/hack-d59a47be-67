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
from starlette.staticfiles import StaticFiles

from . import contracts as c
from .ai_adapter import AIAdapter
from .auth import COOKIE, digest, login, require_employee, require_hr, require_user
from .config import Settings
from .database import Database, MigrationError
from .errors import APIError
from .hr_routes import register_hr_routes
from .imports import ImportService
from .public_demo import (
    MAX_BODY_BYTES,
    WORKSPACE_COOKIE,
    PublicWorkspaces,
    WorkspaceDatabase,
    register_public_routes,
)
from .service import CareerService

log = logging.getLogger("career_quest")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database = WorkspaceDatabase(config) if config.public_demo_mode else Database(config)
    public = PublicWorkspaces(database) if config.public_demo_mode else None
    adapter = AIAdapter(config.ai_enabled)
    career = CareerService(database, adapter)
    importer = ImportService(database)

    @asynccontextmanager
    async def lifespan(app):
        try:
            database.migrate()
        except (sqlite3.Error, OSError, MigrationError):
            log.error("Database initialization failed; readiness is unavailable")
        if public:
            public.initialize()
        yield

    app = FastAPI(
        title="Career Quest API",
        version="1.0.0",
        lifespan=lifespan,
        description="Career Quest demo backend: protected profiles, progression, atomic import, "
        "idempotent completions and persisted recommendations. AI is optional and explicitly disabled by default. "
        "Preview is a team addition. All arithmetic is performed by the backend.",
    )
    app.state.settings, app.state.database, app.state.ai_adapter = config, database, adapter
    app.state.career = career
    app.state.public_demo = public
    register_hr_routes(app, career)
    register_public_routes(app, public)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
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
        context_token = None
        try:
            if public:
                context_token = database.current.set(public.resolve(request.cookies.get(WORKSPACE_COOKIE)))
                if request.method in {"POST", "PATCH", "PUT"}:
                    chunks, length = [], 0
                    async for chunk in request.stream():
                        length += len(chunk)
                        if length > MAX_BODY_BYTES:
                            raise APIError(413, "DEMO_UPLOAD_LIMIT", "Demo requests are limited to 256 KB.")
                        chunks.append(chunk)
                    # Starlette replays this bounded body to the endpoint's JSON parser.
                    request._body = b"".join(chunks)
                if request.url.path == "/api/auth/login":
                    raise APIError(403, "PUBLIC_DEMO_REQUIRED", "Use the public demonstration entry.")
            response = await call_next(request)
        except APIError as exc:
            response = error(request, exc.status, exc.code, exc.message, exc.details)
        except (sqlite3.Error, OSError):
            response = error(request, 503, "STORAGE_UNAVAILABLE", "Storage is temporarily unavailable.")
        except Exception:
            # Do not log request bodies, credentials, raw profiles or exception text.
            log.error("Unhandled request failure request_id=%s", request.state.request_id)
            response = error(request, 500, "INTERNAL_ERROR", "An internal error occurred.")
        finally:
            if context_token is not None:
                database.current.reset(context_token)
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
        try:
            dataset = career.capabilities()
        except (sqlite3.Error, OSError):
            dataset = {"status": "not_loaded", "version": None}
        return {"ai": adapter.capability, "dataset": dataset}

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

    domain_errors = {status: {"model": c.ErrorResponse} for status in [401, 403, 404, 409, 422, 503]}
    domain_options = dict(responses=domain_errors, tags=["implemented"], openapi_extra=implemented)

    @app.get("/api/catalog", response_model=c.CatalogResponse, **domain_options)
    def catalog(user=Depends(require_user)):
        return career.catalog()

    @app.get("/api/employees", response_model=c.EmployeeListResponse, **domain_options)
    def employees(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        user=Depends(require_hr),
    ):
        return career.employees(limit, offset)

    @app.get("/api/employees/{employee_id}", response_model=c.EmployeeDetailResponse, **domain_options)
    def employee(employee_id: str, user=Depends(require_employee)):
        return career.detail(employee_id)

    @app.patch("/api/employees/{employee_id}/goal", response_model=c.EmployeeDetailResponse, **domain_options)
    def goal(employee_id: str, payload: c.GoalUpdateRequest, user=Depends(require_employee)):
        return career.set_goal(employee_id, payload)

    @app.post(
        "/api/employees/{employee_id}/recommendations",
        response_model=c.RecommendationResponse,
        **domain_options,
    )
    async def recommendations(
        employee_id: str, payload: c.RecommendationRequest, user=Depends(require_employee)
    ):
        return await career.recommend(employee_id, payload)

    @app.get(
        "/api/employees/{employee_id}/recommendations/latest",
        response_model=c.RecommendationResponse,
        **domain_options,
    )
    def latest(employee_id: str, user=Depends(require_employee)):
        return career.latest(employee_id)

    @app.post("/api/employees/{employee_id}/preview", response_model=c.PreviewResponse, **domain_options)
    def preview(employee_id: str, payload: c.PreviewRequest, user=Depends(require_employee)):
        return career.preview(employee_id, payload)

    @app.post(
        "/api/employees/{employee_id}/completions", response_model=c.CompletionResponse, **domain_options
    )
    def completions(
        employee_id: str, payload: c.CompletionRequest, request: Request, user=Depends(require_employee)
    ):
        return career.complete(employee_id, user, payload, request.headers.get("Idempotency-Key"))

    @app.get("/api/hr/summary", response_model=c.HRSummaryResponse, **domain_options)
    def summary(user=Depends(require_hr)):
        return career.summary()

    @app.post("/api/hr/import", response_model=c.ImportResponse, **domain_options)
    def import_data(payload: c.ImportRequest | c.BatchImportRequest, user=Depends(require_hr)):
        if isinstance(payload, c.BatchImportRequest):
            files = [file.model_dump(mode="json") for file in payload.files]
        else:
            files = [{key: getattr(payload, key) for key in ("source_filename", "source_format", "content")}]
        if payload.dry_run:
            return importer.preview(files)
        if not payload.preview_token:
            raise APIError(409, "PREVIEW_REQUIRED", "Validate the same batch with dry_run=true first.")
        return importer.commit(files, payload.preview_token)

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
                    path.startswith("/api/")
                    and not path.startswith("/api/public/examples/")
                    and path
                    not in {
                        "/api/auth/login",
                        "/api/health",
                        "/api/health/ready",
                        "/api/version",
                        "/api/public/config",
                        "/api/public/session",
                    }
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
        operation = document["paths"]["/api/employees/{employee_id}/completions"]["post"]
        if not any(item.get("name") == "Idempotency-Key" for item in operation.get("parameters", [])):
            operation.setdefault("parameters", []).append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    "description": "Reuse with the identical body after a lost response; checked after authorization and before revision.",
                }
            )
        return document

    app.openapi = openapi
    if config.static_files_path is not None:
        # The build directory is explicit; never expose the repository or local data.
        app.mount("/", StaticFiles(directory=config.static_files_path, html=True), name="frontend")
    return app


app = create_app()
