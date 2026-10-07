"""Render ordered text blocks to Markdown with heading inference."""

from collections import Counter
from typing import List, Optional

from port_tariff.ingestion.layout_classifier import PageLayoutType
from port_tariff.ingestion.layout_extractor import TextBlock


def _detect_heading_level(block: TextBlock, median_size: float) -> Optional[int]:
    """Infer Markdown heading level from font size relative to page median.

    Only ratios >= 1.15 qualify for headings. A ratio of 1.0 promotes
    every body block to a heading, polluting the document with spurious
    H5/H6 markers that break downstream chunking. Pure page numbers
    (all-digit short text) are also rejected.
    """
    if not block.font_sizes:
        return None
    text = block.text.strip()
    if not text or len(text) < 2:
        return None
    if text.isdigit() and len(text) <= 4:
        return None
    block_size = max(block.font_sizes)
    ratios = [1.8, 1.5, 1.3, 1.15]
    for level, ratio in enumerate(ratios, start=1):
        if block_size >= median_size * ratio and len(block.text) <= 120:
            return level
    return None


def render_blocks_to_markdown(
    blocks: List[TextBlock],
    page_number: int,
    layout_type: PageLayoutType,
) -> str:
    if not blocks:
        return ""

    all_sizes = []
    for b in blocks:
        all_sizes.extend(b.font_sizes)
    median_size = Counter(all_sizes).most_common(1)[0][0] if all_sizes else 10.0

    parts: List[str] = ["<!-- page " + str(page_number) + " -->"]
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        level = _detect_heading_level(block, median_size)
        if level:
            parts.append(("#" * level) + " " + text)
        else:
            parts.append(text)
    return "\n\n".join(parts)
