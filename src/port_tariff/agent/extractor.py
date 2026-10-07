from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from port_tariff.agent.client import LLMClient
from port_tariff.agent.prompts import (
    SYSTEM_TARIFF_PROMPT,
    USER_EXTRACTION_PROMPT_TEMPLATE,
)
from port_tariff.config import get_settings
from port_tariff.exceptions import PortTariffError, RuleExtractionError
from port_tariff.logging_config import get_logger
from port_tariff.models.catalog import ChargeCatalog, ChargeEntry
from port_tariff.models.tariff import (
    ChargeExtractionResult,
    TariffExtractionResult,
    TariffRule,
)
from port_tariff.models.vessel import VesselProfile

logger = get_logger(__name__)


class TariffDiscoveryAgent:
    """Prices every charge of the discovered catalog for a specific vessel call.

    One focused LLM call per charge (run concurrently), so an ambiguous section
    or a failed call affects a single charge instead of the whole extraction.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()
        self.max_workers = max(1, get_settings().llm_max_concurrency)

    def _extract_charge(
        self,
        entry: ChargeEntry,
        catalog: ChargeCatalog,
        context: str,
        vessel: VesselProfile,
    ) -> List[TariffRule]:
        specs, ops = vessel.technical_specs, vessel.operational_data
        user_prompt = USER_EXTRACTION_PROMPT_TEMPLATE.format(
            display_name=entry.display_name,
            aliases=", ".join(entry.aliases) or "-",
            description=entry.description,
            applies_when=entry.applies_when,
            currency=catalog.currency,
            rate_selection_notes=catalog.rate_selection_notes,
            port_name=vessel.port_name or "not specified (single-port document)",
            vessel_name=vessel.vessel_metadata.name,
            vessel_type=specs.type,
            gross_tonnage=specs.gross_tonnage,
            net_tonnage=specs.net_tonnage or "N/A",
            dwt=specs.dwt,
            loa=specs.loa_meters,
            beam=specs.beam_meters,
            activity=ops.activity or "Standard Cargo Call",
            cargo_quantity=ops.cargo_quantity_mt or 0,
            days_alongside=ops.days_alongside or "N/A",
            total_stay_days=vessel.billable_stay_days,
            num_operations=ops.num_operations,
            tariff_markdown=context,
        )
        result = self.llm.extract_structured(
            system_prompt=SYSTEM_TARIFF_PROMPT,
            user_prompt=user_prompt,
            response_model=ChargeExtractionResult,
        )
        if not result.applicable:
            logger.info(
                "agent.charge.not_applicable",
                charge=entry.category,
                reason=result.reason,
            )
            return []
        if not result.rules:
            raise RuleExtractionError("marked applicable but no rule was returned")

        for rule in result.rules:
            rule.canonical_category = entry.category
            rule.item_name = entry.display_name
            rule.currency = rule.currency.strip() or catalog.currency
        return result.rules

    def discover_rules(
        self,
        catalog: ChargeCatalog,
        contexts: List[str],
        vessel: VesselProfile,
    ) -> Tuple[TariffExtractionResult, List[str]]:
        """Returns the extracted rules plus warnings for charges that could not be priced."""
        logger.info(
            "agent.discover.start",
            port=vessel.port_name,
            vessel=vessel.vessel_metadata.name,
            charges=len(catalog.charges),
        )
        if len(contexts) != len(catalog.charges):
            raise RuleExtractionError(
                f"Retriever returned {len(contexts)} contexts for "
                f"{len(catalog.charges)} catalog charges; refusing to "
                "silently drop charges."
            )
        rules: List[TariffRule] = []
        warnings: List[str] = []
        jobs = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for entry, context in zip(catalog.charges, contexts):
                if not context.strip():
                    warnings.append(
                        f"{entry.display_name}: no tariff text was retrieved for this charge."
                    )
                    continue
                jobs.append(
                    (
                        entry,
                        pool.submit(
                            self._extract_charge, entry, catalog, context, vessel
                        ),
                    )
                )
            for entry, future in jobs:
                try:
                    rules.extend(future.result())
                except PortTariffError as exc:
                    logger.warning(
                        "agent.charge.failed", charge=entry.category, error=str(exc)
                    )
                    warnings.append(f"{entry.display_name}: {exc}")

        if not rules:
            detail = f" Issues: {'; '.join(warnings)}" if warnings else ""
            raise RuleExtractionError(
                "No applicable tariff rules could be extracted from the document."
                + detail
            )

        logger.info(
            "agent.discover.completed",
            rule_count=len(rules),
            categories=sorted({r.canonical_category for r in rules}),
            warnings=len(warnings),
        )
        return TariffExtractionResult(port_name=vessel.port_name, rules=rules), warnings
