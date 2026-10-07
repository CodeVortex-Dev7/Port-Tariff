import hashlib
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.documents import Document

from port_tariff.agent.client import LLMClient
from port_tariff.agent.prompts import (
    DISCOVERY_SYSTEM_PROMPT,
    DISCOVERY_USER_PROMPT_TEMPLATE,
)
from port_tariff.exceptions import RuleExtractionError
from port_tariff.logging_config import get_logger
from port_tariff.models.catalog import ChargeCatalog, ChargeEntry

logger = get_logger(__name__)

PREVIEW_CHARS = 160


def _union(first: List[str], second: List[str]) -> List[str]:
    return list(dict.fromkeys([*first, *second]))


class ChargeDiscoveryAgent:
    """Reads an outline of the tariff and builds the catalog of charges it defines.

    Nothing about the charges is assumed up front: names, sections, retrieval
    queries and the document currency all come from the document itself. The
    catalog is cached per document content (and prompt), so the LLM runs once.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        cache_dir: Path = Path("data/catalogs"),
        max_outline_chars: int = 80_000,
    ):
        self.llm = llm_client or LLMClient()
        self.cache_dir = cache_dir
        self.max_outline_chars = max_outline_chars

    def _cache_path(self, document_id: str, markdown_text: str) -> Path:
        digest = hashlib.sha256(
            (markdown_text + DISCOVERY_SYSTEM_PROMPT).encode("utf-8")
        ).hexdigest()[:12]
        clean_id = "".join(c if c.isalnum() else "_" for c in document_id)
        return self.cache_dir / f"{clean_id}_{digest}.json"

    @staticmethod
    def _outline(chunks: List[Document]) -> List[str]:
        lines = []
        for chunk in chunks:
            hint = chunk.metadata.get("section_hint", "")
            body = chunk.page_content
            if hint and body.startswith(hint):
                body = body[len(hint) :]
            lines.append(f"{hint} :: {' '.join(body.split())[:PREVIEW_CHARS]}")
        return lines

    def _windows(self, lines: List[str]) -> List[List[str]]:
        windows: List[List[str]] = []
        current: List[str] = []
        size = 0
        for line in lines:
            if current and size + len(line) > self.max_outline_chars:
                windows.append(current)
                current, size = [], 0
            current.append(line)
            size += len(line) + 1
        if current:
            windows.append(current)
        return windows

    @staticmethod
    def _merge(catalogs: List[ChargeCatalog]) -> ChargeCatalog:
        merged: Dict[str, ChargeEntry] = {}
        for catalog in catalogs:
            for entry in catalog.charges:
                known = merged.get(entry.category)
                if known is None:
                    merged[entry.category] = entry
                    continue
                known.aliases = _union(known.aliases, entry.aliases)
                known.section_refs = _union(known.section_refs, entry.section_refs)
                known.search_queries = _union(
                    known.search_queries, entry.search_queries
                )
        return ChargeCatalog(
            currency=catalogs[0].currency,
            rate_selection_notes=catalogs[0].rate_selection_notes,
            charges=list(merged.values()),
        )

    def discover(
        self,
        document_id: str,
        markdown_text: str,
        chunks: List[Document],
        force_refresh: bool = False,
    ) -> ChargeCatalog:
        """Returns the document's charge catalog, from cache when available."""
        cache_path = self._cache_path(document_id, markdown_text)
        if not force_refresh and cache_path.is_file():
            try:
                catalog = ChargeCatalog.model_validate_json(
                    cache_path.read_text(encoding="utf-8")
                )
                logger.info("agent.discovery.cache_hit", path=str(cache_path))
                return catalog
            except ValueError:
                logger.warning("agent.discovery.cache_invalid", path=str(cache_path))

        windows = self._windows(self._outline(chunks))
        catalogs = []
        for index, window in enumerate(windows, start=1):
            part_note = (
                ""
                if len(windows) == 1
                else (
                    f"This is part {index} of {len(windows)} of the outline; "
                    "list only the charges defined in this part.\n"
                )
            )
            catalogs.append(
                self.llm.extract_structured(
                    system_prompt=DISCOVERY_SYSTEM_PROMPT,
                    user_prompt=DISCOVERY_USER_PROMPT_TEMPLATE.format(
                        document_id=document_id,
                        part_note=part_note,
                        outline="\n".join(window),
                    ),
                    response_model=ChargeCatalog,
                )
            )

        catalog = self._merge(catalogs)
        if not catalog.charges:
            raise RuleExtractionError(
                "No vessel charges could be discovered in the tariff document."
            )

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
        logger.info(
            "agent.discovery.completed",
            windows=len(windows),
            currency=catalog.currency,
            charges=[c.category for c in catalog.charges],
        )
        return catalog
