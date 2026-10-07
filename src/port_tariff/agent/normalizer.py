import json
import re
from pathlib import Path
from typing import Dict, Optional

from port_tariff.exceptions import ConfigurationError
from port_tariff.logging_config import get_logger
from port_tariff.models.catalog import ChargeCatalog
from port_tariff.models.tariff import slugify_category

logger = get_logger(__name__)


class CategoryNormalizer:
    """Maps discovered charges onto a client-defined set of standard categories.

    Driven entirely by an optional JSON file {"STANDARD_NAME": ["alias", ...]}.
    Without the file (or for charges that match no alias) the categories found in
    the document are kept untouched, so nothing here limits what can be discovered.
    """

    def __init__(self, aliases_path: Optional[Path] = None):
        self._lookup: Dict[str, str] = {}
        if not aliases_path:
            return
        path = Path(aliases_path)
        if not path.is_file():
            logger.info("normalizer.aliases_missing", path=str(path))
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for standard, aliases in data.items():
                for name in (standard, *aliases):
                    self._lookup[self._key(name)] = slugify_category(standard)
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            raise ConfigurationError(
                f"Invalid category aliases file '{path}': {exc}"
            ) from exc

    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    def apply(self, catalog: ChargeCatalog) -> ChargeCatalog:
        """Rewrites each entry's category when one of its names matches an alias."""
        for entry in catalog.charges:
            for name in (entry.category, entry.display_name, *entry.aliases):
                standard = self._lookup.get(self._key(name))
                if standard:
                    entry.category = standard
                    break
        return catalog
