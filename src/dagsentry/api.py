"""DagSentry HTTP API."""

import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dagsentry import __version__
from dagsentry.config import Settings, get_settings
from dagsentry.db import SessionFactory, create_session_factory
from dagsentry.metrics import PROMETHEUS_CONTENT_TYPE, record_counter, render_prometheus_metrics
from dagsentry.observability import configure_logging, reset_correlation_id, set_correlation_id
from dagsentry.routes.admin import router as admin_router
from dagsentry.routes.admin_connections import router as admin_connections_router
from dagsentry.routes.auth import LoginRateLimiter
from dagsentry.routes.auth import router as auth_router
from dagsentry.routes.daily_report_schedules import (
    admin_router as daily_report_schedules_admin_router,
)
from dagsentry.routes.daily_report_schedules import router as daily_report_schedules_router
from dagsentry.routes.daily_reports import router as daily_reports_router
from dagsentry.routes.diagnoses import router as diagnoses_router
from dagsentry.routes.error_signatures import router as error_signatures_router
from dagsentry.routes.failures import router as failures_router
from dagsentry.routes.human_diagnoses import router as human_diagnoses_router
from dagsentry.routes.incidents import router as incidents_router

logger = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).with_name("web")
WEB_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class IngestBodyLimitMiddleware:
    """Reject an ingestion request once its received body exceeds the configured limit."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/api/v1/failure-events"
        ):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope["headers"] if name == b"content-length"), None
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await _payload_too_large_response(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})(
                    scope, receive, send
                )
                return

        received_bytes = 0
        body_parts: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, _single_message_receive(message), send)
                return
            body = message.get("body", b"")
            body_parts.append(body)
            received_bytes += len(body)
            if received_bytes > self.max_bytes:
                await _payload_too_large_response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            return {
                "type": "http.request",
                "body": b"".join(body_parts),
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)


def _single_message_receive(message: Message) -> Receive:
    async def receive() -> Message:
        return message

    return receive


async def _payload_too_large_response(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={"detail": "Failure Event payload is too large"},
    )(scope, receive, send)


def create_app(
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
) -> FastAPI:
    """Create the DagSentry API application."""
    runtime_settings = settings or get_settings()
    application = FastAPI(title="DagSentry", version=__version__)
    application.state.settings = runtime_settings
    application.state.session_factory = session_factory or create_session_factory(
        runtime_settings.database_url
    )
    application.state.login_rate_limiter = LoginRateLimiter(
        runtime_settings.login_rate_limit_attempts,
        runtime_settings.login_rate_limit_window_seconds,
    )
    application.add_middleware(
        IngestBodyLimitMiddleware,
        max_bytes=runtime_settings.ingest_max_request_bytes,
    )
    application.include_router(auth_router)
    application.include_router(admin_router)
    application.include_router(admin_connections_router)
    application.include_router(failures_router)
    application.include_router(incidents_router)
    application.include_router(human_diagnoses_router)
    application.include_router(error_signatures_router)
    application.include_router(diagnoses_router)
    application.include_router(daily_reports_router)
    application.include_router(daily_report_schedules_router)
    application.include_router(daily_report_schedules_admin_router)
    application.mount("/ui", StaticFiles(directory=WEB_ROOT, html=True), name="web-ui")

    @application.middleware("http")
    async def secure_web_ui(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path == "/ui" or request.url.path.startswith("/ui/"):
            response.headers["Content-Security-Policy"] = WEB_CONTENT_SECURITY_POLICY
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.middleware("http")
    async def correlate_request(request: Request, call_next: Any) -> Any:
        supplied_id = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = str(UUID(supplied_id)) if supplied_id is not None else str(uuid4())
        except ValueError:
            correlation_id = str(uuid4())

        context_token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            logger.info("request completed")
            return response
        finally:
            reset_correlation_id(context_token)

    @application.middleware("http")
    async def count_ingest_failures(request: Request, call_next: Any) -> Any:
        is_ingest = request.method == "POST" and request.url.path == "/api/v1/failure-events"
        try:
            response = await call_next(request)
        except Exception:
            if is_ingest:
                record_counter(
                    request.app.state.session_factory,
                    "dagsentry_ingest_events_total",
                    "failure",
                )
            raise
        if is_ingest and response.status_code >= 400:
            record_counter(
                request.app.state.session_factory,
                "dagsentry_ingest_events_total",
                "failure",
            )
        return response

    @application.get("/health/live", tags=["health"])
    def liveness() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @application.get("/health/ready", tags=["health"])
    def readiness(request: Request) -> dict[str, str]:
        try:
            with request.app.state.session_factory() as session:
                checked_session: Session = session
                checked_session.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database is not ready",
            ) from error
        return {"status": "ready"}

    @application.get("/", include_in_schema=False)
    def web_ui_root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    @application.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> Response:
        return Response(
            render_prometheus_metrics(request.app.state.session_factory),
            headers={"Content-Type": PROMETHEUS_CONTENT_TYPE},
        )

    return application


app = create_app()


def main() -> None:
    """Run the DagSentry API development server."""
    settings = get_settings()
    configure_logging("api", settings.log_level)
    uvicorn.run("dagsentry.api:app", host="0.0.0.0", port=8000, log_config=None)
