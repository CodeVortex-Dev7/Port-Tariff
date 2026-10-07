import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from port_tariff.api.errors import register_exception_handlers
from port_tariff.api.routes import router as tariff_router
from port_tariff.config import get_settings
from port_tariff.logging_config import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    get_logger,
)

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Binds a request_id and emits entry/exit logs with latency."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        clear_request_context()
        bind_request_context(request_id=request_id)
        start = time.perf_counter()

        logger.info(
            "http.request.received",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else None,
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "http.request.failed",
                method=request.method,
                path=request.url.path,
            )
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        debug=settings.app_debug,
    )

    app = FastAPI(
        title="Autonomous Port Tariff Calculation API",
        description=(
            "Automated system consuming complex Port Tariff PDFs and vessel profiles "
            "to extract and calculate port dues deterministically."
        ),
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(tariff_router, prefix="/api/v1")

    logger.info(
        "app.initialized",
        env=settings.app_env,
        debug=settings.app_debug,
        log_level=settings.log_level,
        log_format=settings.log_format,
    )
    return app


app = create_app()


def main():
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "port_tariff.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_debug,
    )


if __name__ == "__main__":
    main()
