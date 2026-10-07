import hashlib
from pathlib import Path
from typing import Optional

from markitdown import MarkItDown

from port_tariff.config import get_settings
from port_tariff.exceptions import DocumentParsingError
from port_tariff.ingestion.layout_aware_converter import (
    LayoutAwarePDFConverter,
)
from port_tariff.logging_config import get_logger, timed

logger = get_logger(__name__)


class TariffDocumentParser:
    """Extracts and caches clean Markdown from port tariff documents.

    PDFs are routed through the layout-aware converter (pdfminer +
    pdfplumber + XY-Cut) so multi-column reading order and ruled tables
    survive conversion; other formats fall back to Microsoft's MarkItDown.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.settings = get_settings()
        self.cache_dir = cache_dir or Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Initialize MarkItDown converter (non-PDF fallback) and the
        # layout-aware PDF converter (handles multi-column tariff pages).
        self._converter = MarkItDown(enable_plugins=False)
        self._pdf_converter = LayoutAwarePDFConverter()
        logger.debug(
            "parser.initialized",
            cache_dir=str(self.cache_dir),
            cache_enabled=self.settings.cache_parsed_markdown,
        )

    def _get_cache_path(self, file_path: Path) -> Path:
        """Computes a SHA-256 hash of the file to determine its cached Markdown path."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()[:16]
        return self.cache_dir / f"{file_path.stem}_{file_hash}.md"

    def parse_to_markdown(
        self, file_path: Path, force_refresh: bool = False
    ) -> str:
        """Converts the tariff PDF/document to Markdown.

        Returns the cached version if available.
        """
        path = Path(file_path)
        if not path.is_file():
            logger.error("parser.file.not_found", path=str(path))
            raise FileNotFoundError(f"Tariff document not found at: {path}")

        cache_path = self._get_cache_path(path)
        logger.info(
            "parser.parse.start",
            path=str(path),
            size_bytes=path.stat().st_size,
            cache_path=str(cache_path),
            force_refresh=force_refresh,
        )

        # Return cached content if caching is enabled and cache file exists
        if (
            self.settings.cache_parsed_markdown
            and not force_refresh
            and cache_path.exists()
        ):
            content = cache_path.read_text(encoding="utf-8")
            logger.info(
                "parser.parse.cache_hit",
                path=str(path),
                chars=len(content),
            )
            return content

        reason = (
            "disabled"
            if not self.settings.cache_parsed_markdown
            else ("force_refresh" if force_refresh else "cache_miss")
        )
        with timed(
            logger,
            "parser.parse.converted",
            path=str(path),
            cache_state=reason,
        ) as extra:
            try:
                if path.suffix.lower() == ".pdf":
                    content = self._pdf_converter.convert_to_markdown(str(path))
                    extra["converter"] = "layout_aware_pdf"
                else:
                    result = self._converter.convert(str(path))
                    content = getattr(result, "text_content", None) or getattr(
                        result, "markdown", ""
                    )
                    extra["converter"] = "markitdown"
            except Exception as exc:
                logger.exception("parser.parse.failed", path=str(path))
                raise DocumentParsingError(
                    f"Could not read tariff document '{path.name}' "
                    f"({type(exc).__name__}). Ensure it is a valid, "
                    "text-based PDF or a supported document."
                ) from exc
            extra["chars"] = len(content)

        if not content:
            logger.error("parser.parse.empty", path=str(path))
            raise DocumentParsingError(
                f"No extractable text found in '{path.name}' "
                "(scanned/image-only documents are not supported)."
            )

        # Cache the extracted markdown
        if self.settings.cache_parsed_markdown:
            cache_path.write_text(content, encoding="utf-8")
            logger.debug("parser.parse.cached", cache_path=str(cache_path))

        return content
