from functools import lru_cache
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from port_tariff.exceptions import LLMServiceError
from port_tariff.logging_config import bind_request_context, get_logger
from port_tariff.models.response import TariffCalculationResponse
from port_tariff.models.vessel import VesselProfile
from port_tariff.service import PortTariffService

logger = get_logger(__name__)

router = APIRouter(tags=["Port Tariffs"])


@lru_cache
def get_tariff_service() -> PortTariffService:
    """Builds the shared service on first request instead of at import time."""
    return PortTariffService()


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Healthcheck endpoint."""
    return {"status": "healthy", "service": "port-tariff-calculator"}


@router.post(
    "/calculate",
    response_model=TariffCalculationResponse,
    status_code=status.HTTP_200_OK,
    summary="Calculate port dues from Tariff PDF and Vessel Profile",
)
# Sync on purpose: FastAPI runs it in a worker thread, so the blocking
# PDF / embedding / LLM calls do not stall the event loop.
def calculate_tariffs(
    tariff_pdf: UploadFile = File(
        ..., description="The official port tariff document (PDF)"
    ),
    vessel_data: str = Form(
        ...,
        description=(
            "JSON string representing the VesselProfile (specs and operational"
            " data)"
        ),
        examples=[
            '{"port_name": "Durban", "vessel_metadata": {"name":'
            ' "SUDESTADA"}, "technical_specs": {"type": "Bulk Carrier", "dwt":'
            ' 93274, "gross_tonnage": 51300, "loa_meters": 229.2, "beam_meters":'
            ' 38.0}, "operational_data": {"cargo_quantity_mt": 40000,'
            ' "days_alongside": 3.39, "num_operations": 2}}'
        ],
    ),
    tariff_service: PortTariffService = Depends(get_tariff_service),
):
    logger.info(
        "api.calculate.received",
        filename=tariff_pdf.filename,
        content_type=tariff_pdf.content_type,
        vessel_data_chars=len(vessel_data or ""),
    )

    # Parse vessel profile. Three code paths:
    #   1) Valid JSON that satisfies VesselProfile schema -> use directly.
    #   2) Valid JSON that FAILS schema validation    -> 422 (do NOT hallucinate
    #      a vessel via the LLM just because the caller submitted malformed JSON).
    #   3) Non-JSON input                             -> NL fallback with a
    #      hallucination gate on obviously incomplete extractions.
    try:
        raw_json = json.loads(vessel_data)
    except json.JSONDecodeError:
        raw_json = None

    if raw_json is not None:
        try:
            vessel_profile = VesselProfile.model_validate(raw_json)
            logger.debug("api.calculate.vessel_parsed_as_json")
        except Exception as schema_exc:
            logger.warning(
                "api.calculate.vessel_json_invalid_schema",
                reason=type(schema_exc).__name__,
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "Vessel JSON failed schema validation. Provide a valid "
                    "VesselProfile object or a natural-language description. "
                    f"Error: {schema_exc}"
                ),
            )
    else:
        logger.info("api.calculate.vessel_not_json_using_nl_fallback")
        try:
            vessel_profile = tariff_service.parse_natural_language_vessel(vessel_data)
            # Hallucination gate: reject incomplete NL extractions before we
            # spend LLM tokens calculating on fabricated data. Check all
            # quantitatively significant fields, not just name/GT.
            name = (vessel_profile.vessel_metadata.name or "").strip().lower()
            specs = vessel_profile.technical_specs
            problems: List[str] = []
            if name in {"", "unknown", "unknown vessel", "n/a", "none", "vessel"}:
                problems.append("vessel name missing or placeholder")
            if specs.gross_tonnage <= 0:
                problems.append("gross_tonnage missing or zero")
            if specs.dwt <= 0:
                problems.append("dwt missing or zero")
            if specs.loa_meters <= 0:
                problems.append("loa_meters missing or zero")
            if not specs.type or specs.type.strip().lower() in {"", "unknown", "n/a"}:
                problems.append("vessel type missing")
            if problems:
                raise ValueError(
                    f"NL parser returned incomplete vessel profile: {'; '.join(problems)}"
                )
            logger.info("api.calculate.vessel_nl_parse_ok")
        except LLMServiceError:
            raise
        except Exception as e:
            logger.exception("api.calculate.vessel_nl_parse_failed")
            raise HTTPException(
                status_code=422,
                detail=f"Unable to parse vessel data as a natural language query: {str(e)}",
            )

    bind_request_context(
        vessel_name=vessel_profile.vessel_metadata.name,
        port=vessel_profile.port_name,
    )
    logger.info(
        "api.calculate.vessel_ready",
        vessel_type=vessel_profile.technical_specs.type,
        gross_tonnage=vessel_profile.technical_specs.gross_tonnage,
        moves=vessel_profile.operational_data.num_operations,
        days_alongside=vessel_profile.operational_data.days_alongside,
    )

    # Save uploaded PDF to a temporary file
    temp_dir = Path(tempfile.mkdtemp())
    # Never trust the client-supplied filename: keep only a sanitised basename.
    safe_name = (
        re.sub(r"[^A-Za-z0-9._-]", "_", Path(tariff_pdf.filename or "").name).lstrip(".")
        or "tariff.pdf"
    )
    temp_file_path = temp_dir / safe_name

    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(tariff_pdf.file, buffer)
        logger.debug("api.calculate.pdf_saved", path=str(temp_file_path))

        # Process and compute dues
        result = tariff_service.process_tariff_and_calculate(
            tariff_pdf_path=temp_file_path,
            vessel_profile=vessel_profile,
        )
        logger.info(
            "api.calculate.completed",
            total_dues=result.total_dues,
            currency=result.currency,
            item_count=len(result.items),
        )
        return result

    finally:
        # Clean up temporary file
        shutil.rmtree(temp_dir, ignore_errors=True)
