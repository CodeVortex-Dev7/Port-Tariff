"""Top-level layout-aware PDF -> Markdown converter.

Design principle: ruled tables are always extracted via pdfplumber,
regardless of how the page is classified. The classifier's job is only
to choose the reading order for NON-table content (prose, headings,
sidebars). Tables themselves are structural primitives that benefit
from dedicated extraction on every page.

Per-page pipeline:
  1. Attempt pdfplumber ruled-table extraction.
  2. If tables exist:
       - Drop layout-tree blocks whose centre falls inside a table bbox.
       - Partition surviving blocks into horizontal bands delimited by
         the tables' y-ranges.
       - Within each band, apply the page's reading order.
       - Concatenate bands top-down, inserting table Markdown inline.
  3. If no tables exist:
       - Apply the layout_type-specific reading order to all blocks.
"""

from pathlib import Path
from typing import Dict, List, Optional

from port_tariff.ingestion.layout_classifier import (
    PageLayoutType,
    classify_page_layout,
)
from port_tariff.ingestion.layout_extractor import TextBlock, extract_text_blocks
from port_tariff.ingestion.markdown_renderer import render_blocks_to_markdown
from port_tariff.ingestion.table_extractor import find_tables_with_bbox
from port_tariff.ingestion.xy_cut import xy_cut_reading_order
from port_tariff.logging_config import get_logger, timed

logger = get_logger(__name__)


def _order_band(
    band_blocks: List[TextBlock], layout_type: PageLayoutType
) -> List[TextBlock]:
    """Apply reading order to a band of blocks.

    XY-Cut is applied unconditionally. It degenerates to a
    top-to-bottom sort for single-column regions and correctly
    recovers column order for multi-column regions, so it is safe
    for any layout the page classifier might have chosen. The
    layout_type parameter is retained for API stability but no
    longer gates the algorithm.
    """
    if len(band_blocks) <= 1:
        return band_blocks
    order = xy_cut_reading_order(band_blocks)
    return [band_blocks[i] for i in order]


def _render_page_with_tables(
    blocks: List[TextBlock],
    page_height: float,
    tables_with_bbox,
    page_number: int,
    layout_type: PageLayoutType,
) -> str:
    """Render a page interleaving layout-tree blocks with pdfplumber tables.

    Coordinate systems:
      - pdfminer block coords: bottom-left origin, y increases up.
      - pdfplumber bbox coords: top-left origin, y increases down.

    We convert pdfplumber bboxes into pdfminer's coordinate space so both
    can be compared on a single y-axis.
    """
    converted = []
    for bbox, md in tables_with_bbox:
        x0, top, x1, bottom = bbox
        converted.append(
            {
                "x0": float(x0),
                "x1": float(x1),
                "y_bottom": float(page_height - bottom),
                "y_top": float(page_height - top),
                "markdown": md,
            }
        )

    def inside_any_table(b: TextBlock) -> bool:
        cx = (b.x0 + b.x1) / 2.0
        cy = (b.y0 + b.y1) / 2.0
        return any(
            t["x0"] <= cx <= t["x1"] and t["y_bottom"] <= cy <= t["y_top"]
            for t in converted
        )

    surviving = [b for b in blocks if not inside_any_table(b)]
    tables_sorted = sorted(converted, key=lambda t: -t["y_top"])

    parts: List[str] = ["<!-- page " + str(page_number) + " -->"]
    consumed_ids: set = set()

    for i, t in enumerate(tables_sorted):
        prev_bottom = (
            tables_sorted[i - 1]["y_bottom"] if i > 0 else float("inf")
        )
        above = [
            b
            for b in surviving
            if b.y0 >= t["y_top"] and b.y1 <= prev_bottom
        ]
        if above:
            parts.append(
                render_blocks_to_markdown(
                    _order_band(above, layout_type), page_number, layout_type
                )
            )
            consumed_ids.update(id(b) for b in above)
        parts.append(t["markdown"])

    last_bottom = tables_sorted[-1]["y_bottom"]
    below = [b for b in surviving if b.y1 <= last_bottom]
    if below:
        parts.append(
            render_blocks_to_markdown(
                _order_band(below, layout_type), page_number, layout_type
            )
        )
        consumed_ids.update(id(b) for b in below)

    # Safety net: any surviving block not assigned to a band (unusual
    # geometry, e.g. sidebars) is appended so no content is silently lost.
    leftovers = [b for b in surviving if id(b) not in consumed_ids]
    if leftovers:
        parts.append(
            render_blocks_to_markdown(
                _order_band(leftovers, layout_type), page_number, layout_type
            )
        )

    return "\n\n".join(parts)


class LayoutAwarePDFConverter:
    """Convert PDF to Markdown with correct multi-column reading order."""

    def convert_to_markdown(self, pdf_path: str) -> str:
        path = Path(pdf_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF not found: {path}")

        with timed(logger, "layout_converter.start", path=str(path)) as extra:
            pages = extract_text_blocks(str(path))
            page_mds: List[str] = []
            layout_counts: Dict[str, int] = {}
            pages_with_tables = 0

            for page_num, blocks in enumerate(pages, start=1):
                if not blocks:
                    continue

                profile = classify_page_layout(blocks)
                layout_counts[profile.layout_type.value] = (
                    layout_counts.get(profile.layout_type.value, 0) + 1
                )

                # Always attempt ruled-table extraction. A page can be
                # dominated by prose and still contain a rate table.
                tables = find_tables_with_bbox(str(path), page_num - 1)
                if tables:
                    pages_with_tables += 1
                    page_mds.append(
                        _render_page_with_tables(
                            blocks,
                            blocks[0].page_height,
                            tables,
                            page_num,
                            profile.layout_type,
                        )
                    )
                    continue

                # No ruled tables on this page: apply layout-specific order.
                ordered = _order_band(blocks, profile.layout_type)
                page_mds.append(
                    render_blocks_to_markdown(
                        ordered, page_num, profile.layout_type
                    )
                )

            content = "\n\n".join(page_mds)
            extra["pages"] = len(pages)
            extra["pages_with_tables"] = pages_with_tables
            extra["chars"] = len(content)
            extra["layout_counts"] = layout_counts

        return content
