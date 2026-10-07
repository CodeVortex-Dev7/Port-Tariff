from pathlib import Path
from typing import Optional, Union

from port_tariff.agent.discovery import ChargeDiscoveryAgent
from port_tariff.agent.extractor import TariffDiscoveryAgent
from port_tariff.agent.normalizer import CategoryNormalizer
from port_tariff.config import get_settings
from port_tariff.engine.calculator import DeterministicTariffEngine
from port_tariff.ingestion.parser import TariffDocumentParser
from port_tariff.logging_config import get_logger, timed
from port_tariff.models.response import TariffCalculationResponse
from port_tariff.models.vessel import VesselProfile
from port_tariff.rag.retriever import TariffRetriever

logger = get_logger(__name__)


class PortTariffService:
    """End-to-end orchestration service for automated port tariff calculation

    using Agentic RAG (ChromaDB + Multi-Category Retrieval).
    """

    def __init__(
        self,
        parser: Optional[TariffDocumentParser] = None,
        agent: Optional[TariffDiscoveryAgent] = None,
        engine: Optional[DeterministicTariffEngine] = None,
        retriever: Optional[TariffRetriever] = None,
        discovery: Optional[ChargeDiscoveryAgent] = None,
        normalizer: Optional[CategoryNormalizer] = None,
    ):
        self.parser = parser or TariffDocumentParser()
        self.agent = agent or TariffDiscoveryAgent()
        self.engine = engine or DeterministicTariffEngine()
        self.retriever = retriever or TariffRetriever()
        self.discovery = discovery or ChargeDiscoveryAgent(llm_client=self.agent.llm)
        aliases_path = get_settings().category_aliases_path
        self.normalizer = normalizer or CategoryNormalizer(
            Path(aliases_path) if aliases_path else None
        )

    def process_tariff_and_calculate(
        self,
        tariff_pdf_path: Union[str, Path],
        vessel_profile: VesselProfile,
        force_reparse: bool = False,
    ) -> TariffCalculationResponse:
        """1. Converts the tariff document to structured Markdown (layout-aware
           converter for PDFs, MarkItDown for other supported formats).

        2. Indexes markdown into ChromaDB and retrieves relevant multi-category sections.
        3. Discovers and extracts statutory calculation rules via the LLM Agent.
        4. Executes exact deterministic mathematics to compute all dues.
        """
        pdf_path = Path(tariff_pdf_path)
        logger.info(
            "pipeline.start",
            document=pdf_path.name,
            port=vessel_profile.port_name,
            vessel=vessel_profile.vessel_metadata.name,
            force_reparse=force_reparse,
        )

        with timed(logger, "pipeline.parse.completed", document=pdf_path.name) as extra:
            tariff_markdown = self.parser.parse_to_markdown(
                pdf_path, force_refresh=force_reparse
            )
            extra["chars"] = len(tariff_markdown)

        document_id = pdf_path.stem

        with timed(
            logger,
            "pipeline.discover.completed",
            document=pdf_path.name,
        ) as extra:
            chunks = self.retriever.get_chunks(document_id, tariff_markdown)
            catalog = self.discovery.discover(
                document_id=document_id,
                markdown_text=tariff_markdown,
                chunks=chunks,
                force_refresh=force_reparse,
            )
            self.normalizer.apply(catalog)
            extra["charges"] = [c.category for c in catalog.charges]

        with timed(
            logger,
            "pipeline.retrieve.completed",
            port=vessel_profile.port_name,
        ) as extra:
            contexts = self.retriever.retrieve_context(
                document_id=document_id,
                markdown_text=tariff_markdown,
                catalog=catalog,
                chunks=chunks,
            )
            extra["chars"] = sum(len(c) for c in contexts)

        with timed(
            logger,
            "pipeline.extract.completed",
            vessel=vessel_profile.vessel_metadata.name,
        ) as extra:
            extraction_result, warnings = self.agent.discover_rules(
                catalog=catalog,
                contexts=contexts,
                vessel=vessel_profile,
            )
            extra["rule_count"] = len(extraction_result.rules)
            extra["categories"] = sorted(
                {r.canonical_category for r in extraction_result.rules}
            )

        with timed(
            logger,
            "pipeline.calculate.completed",
            rule_count=len(extraction_result.rules),
        ) as extra:
            calculation_response = self.engine.calculate_all(
                rules=extraction_result.rules,
                vessel=vessel_profile,
            )
            extra["total_dues"] = calculation_response.total_dues
            extra["currency"] = calculation_response.currency

        calculation_response.warnings = warnings
        logger.info(
            "pipeline.completed",
            total_dues=calculation_response.total_dues,
            currency=calculation_response.currency,
            item_count=len(calculation_response.items),
        )
        return calculation_response

    def parse_natural_language_vessel(self, query: str) -> VesselProfile:
        """Parses a free-form natural language vessel description into a validated VesselProfile."""
        logger.info("vessel.nl_parse.start", query_chars=len(query))
        system_prompt = (
            "You are an expert maritime port dispatcher. Extract vessel technical specifications, "
            "operational data, and port of call from the user's natural language query into the "
            "exact VesselProfile schema. Infer reasonable defaults for missing optional fields."
        )
        profile = self.agent.llm.extract_structured(
            system_prompt=system_prompt,
            user_prompt=f"Extract vessel call profile from this query:\n\n{query}",
            response_model=VesselProfile,
        )
        logger.info(
            "vessel.nl_parse.completed",
            vessel_name=profile.vessel_metadata.name,
            port=profile.port_name,
        )
        return profile
