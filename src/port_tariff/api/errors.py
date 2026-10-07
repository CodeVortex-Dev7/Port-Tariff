"""Maps domain exceptions to HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from port_tariff.exceptions import (
    ConfigurationError,
    DocumentParsingError,
    InvalidTariffRuleError,
    LLMServiceError,
    PortTariffError,
    RuleExtractionError,
)
from port_tariff.logging_config import get_logger

logger = get_logger(__name__)

_STATUS_BY_ERROR: dict[type[PortTariffError], int] = {
    DocumentParsingError: 422,
    RuleExtractionError: 422,
    InvalidTariffRuleError: 422,
    LLMServiceError: 502,
    ConfigurationError: 500,
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PortTariffError)
    async def handle_domain_error(
        request: Request, exc: PortTariffError
    ) -> JSONResponse:
        status_code = next(
            (
                code
                for error_cls, code in _STATUS_BY_ERROR.items()
                if isinstance(exc, error_cls)
            ),
            500,
        )
        logger.warning(
            "api.domain_error",
            error_type=type(exc).__name__,
            status_code=status_code,
            detail=str(exc),
        )
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})
