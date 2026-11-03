"""HTTP layer: routes, validation and mapping of failures to status codes."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.models import Consumption, ErrorResponse, Meter, MeterPage, Network, Transformer
from app.portal.client import UrjaPortalClient
from app.portal.errors import PortalAuthError, PortalError, PortalTimeoutError, PortalUnavailableError
from app.service import MeterFilters, MeterNotFoundError, MeterService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Meter not found"}}
PORTAL_FAILURES = {
    502: {"model": ErrorResponse, "description": "Portal rejected our credentials or sent an unusable response"},
    503: {"model": ErrorResponse, "description": "Portal unreachable or rate limiting us"},
    504: {"model": ErrorResponse, "description": "Portal timed out"},
}


def create_app(service: MeterService | None = None) -> FastAPI:
    """Build the app. Tests pass a ready-made service; otherwise one is built from settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            app.state.service = service
            yield
            return
        settings = Settings()
        client = UrjaPortalClient(
            settings.portal_url,
            settings.username,
            settings.password.get_secret_value(),
            timeout_seconds=settings.timeout_seconds,
            min_request_interval_seconds=settings.min_request_interval_seconds,
            max_rate_limit_retries=settings.max_rate_limit_retries,
        )
        app.state.service = MeterService(client, settings.cache_ttl_seconds)
        yield
        client.close()

    app = FastAPI(
        title="Urja Meter Ops API",
        version="1.0.0",
        description="A clean, read-only API over the Urja Meter Ops smart-meter portal. "
        "See PROTOCOL.md for how the underlying portal works.",
        lifespan=lifespan,
    )
    _register_error_handlers(app)
    _register_routes(app)
    return app


def get_service(request: Request) -> MeterService:
    return request.app.state.service


ServiceDep = Annotated[MeterService, Depends(get_service)]


def _register_routes(app: FastAPI) -> None:
    @app.get("/health", tags=["meta"], summary="Liveness check (does not contact the portal)")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/meters", response_model=MeterPage, responses=PORTAL_FAILURES, tags=["meters"], summary="List and filter meters")
    def list_meters(
        service: ServiceDep,
        q: Annotated[str | None, Query(description="Substring of meter ID or serial number")] = None,
        install_status: Annotated[str | None, Query(examples=["faulty"])] = None,
        install_type: Annotated[str | None, Query(examples=["ct_operated"])] = None,
        phase_type: Annotated[str | None, Query(examples=["three"])] = None,
        make: Annotated[str | None, Query(examples=["Genus"])] = None,
        dt_code: Annotated[str | None, Query(examples=["DT-001"])] = None,
        feeder_code: Annotated[str | None, Query(examples=["F-001"])] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> MeterPage:
        filters = MeterFilters(q, install_status, install_type, phase_type, make, dt_code, feeder_code)
        return service.list_meters(filters, page, page_size)

    @app.get(
        "/meters/{meter_id}",
        response_model=Meter,
        responses={**NOT_FOUND, **PORTAL_FAILURES},
        tags=["meters"],
        summary="Get one meter with its nameplate, location and network position",
    )
    def get_meter(meter_id: str, service: ServiceDep) -> Meter:
        return service.get_meter(meter_id)

    @app.get(
        "/meters/{meter_id}/consumption",
        response_model=Consumption,
        responses={**NOT_FOUND, **PORTAL_FAILURES},
        tags=["meters"],
        summary="Latest available energy readings for a meter, with per-interval consumption",
    )
    def get_consumption(meter_id: str, service: ServiceDep) -> Consumption:
        return service.get_consumption(meter_id)

    @app.get(
        "/network",
        response_model=Network,
        responses=PORTAL_FAILURES,
        tags=["network"],
        summary="Network hierarchy (zone to DT) with meter counts and a data-quality report",
    )
    def get_network(service: ServiceDep) -> Network:
        return service.get_network()

    @app.get(
        "/transformers",
        response_model=list[Transformer],
        responses=PORTAL_FAILURES,
        tags=["network"],
        summary="List distribution transformers",
    )
    def list_transformers(service: ServiceDep) -> list[Transformer]:
        return service.list_transformers()


def _register_error_handlers(app: FastAPI) -> None:
    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": code, "message": message})

    @app.exception_handler(MeterNotFoundError)
    def meter_not_found(_: Request, exc: MeterNotFoundError) -> JSONResponse:
        return error(404, "meter_not_found", str(exc))

    @app.exception_handler(PortalError)
    def portal_error(_: Request, exc: PortalError) -> JSONResponse:
        # Most specific first: timeouts are a kind of unavailability.
        if isinstance(exc, PortalTimeoutError):
            return error(504, "portal_timeout", str(exc))
        if isinstance(exc, PortalUnavailableError):
            return error(503, "portal_unavailable", str(exc))
        if isinstance(exc, PortalAuthError):
            return error(502, "portal_auth_failed", str(exc))
        return error(502, "portal_bad_response", str(exc))


app = create_app()
