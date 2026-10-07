"""Page layout classification: table detection + genuine column detection.

Key design decision: TABLES and MULTI-COLUMN PROSE both produce multiple
x-bands under naive x-axis projection. We must distinguish them BEFORE
attempting column reconstruction:

  - A TABLE has many SHORT blocks (small height) aligned in a grid
    (row-aligned y-coordinates AND column-aligned x-coordinates).
  - MULTI-COLUMN PROSE has TALL blocks on both sides of a genuine gutter
    that spans a large fraction of the page height.

Classifying tables as multi_column is what causes XY-Cut to shred tabular
rows into scattered cells — the exact failure we are correcting here.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

from port_tariff.ingestion.layout_extractor import TextBlock


class PageLayoutType(str, Enum):
    SINGLE_COLUMN = "single_column"
    MULTI_COLUMN = "multi_column"
    TABLE_HEAVY = "table_heavy"
    COMPLEX_MIXED = "complex_mixed"


@dataclass
class LayoutProfile:
    layout_type: PageLayoutType
    column_boundaries: List[Tuple[float, float]] = field(default_factory=list)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Table detection
# ---------------------------------------------------------------------------

def _is_table_page(blocks: List[TextBlock], pw: float, ph: float) -> bool:
    """Detect a page dominated by table structure.

    Signals:
      1. Enough blocks to form a grid (>= 12).
      2. Row alignment: many distinct y-rows contain >= 3 blocks.
      3. Column alignment: many distinct x-positions contain >= 3 blocks.
      4. Short average block height (< 6% of page height) — table cells are
         typically one-line, unlike prose paragraphs.
    """
    if len(blocks) < 12:
        return False

    # Row alignment: bucket block tops into 2pt bins
    y_rows: dict[int, int] = {}
    for b in blocks:
        key = round(b.y0 / 2.0)
        y_rows[key] = y_rows.get(key, 0) + 1
    multi_cell_rows = sum(1 for v in y_rows.values() if v >= 3)
    if multi_cell_rows < 3:
        return False

    # Column alignment: bucket block lefts into 2pt bins
    x_cols: dict[int, int] = {}
    for b in blocks:
        key = round(b.x0 / 2.0)
        x_cols[key] = x_cols.get(key, 0) + 1
    multi_block_cols = sum(1 for v in x_cols.values() if v >= 3)
    if multi_block_cols < 3:
        return False

    # Short block heights confirm cell-like content
    avg_height = sum(b.height for b in blocks) / len(blocks)
    if avg_height > ph * 0.06:
        return False

    return True


# ---------------------------------------------------------------------------
# Genuine column detection
# ---------------------------------------------------------------------------

def _detect_columns(
    blocks: List[TextBlock], pw: float, ph: float
) -> List[float]:
    """Return x-coordinates of genuine column gutters.

    A gutter qualifies only if:
      - No block's x-range overlaps the gutter.
      - Both sides contain >= 2 blocks.
      - Both sides span >= 35% of the page's vertical extent.
      - The gutter is wider than 4% of the page width.
    """
    if len(blocks) < 4:
        return []

    # Merge overlapping x-intervals to find gaps
    intervals = sorted(((b.x0, b.x1) for b in blocks), key=lambda iv: iv[0])
    merged: List[List[float]] = [[intervals[0][0], intervals[0][1]]]
    for x0, x1 in intervals[1:]:
        if x0 <= merged[-1][1] + 1.0:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])

    if len(merged) < 2:
        return []

    gutters: List[float] = []
    for i in range(len(merged) - 1):
        gap_start = merged[i][1]
        gap_end = merged[i + 1][0]
        if gap_end - gap_start < pw * 0.04:
            continue

        left = [b for b in blocks if b.x1 <= gap_start]
        right = [b for b in blocks if b.x0 >= gap_end]
        if len(left) < 2 or len(right) < 2:
            continue

        left_h = max(b.y1 for b in left) - min(b.y0 for b in left)
        right_h = max(b.y1 for b in right) - min(b.y0 for b in right)
        if left_h < ph * 0.35 or right_h < ph * 0.35:
            continue

        gutters.append((gap_start + gap_end) / 2.0)

    return gutters


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------

def classify_page_layout(blocks: List[TextBlock]) -> LayoutProfile:
    if not blocks:
        return LayoutProfile(PageLayoutType.SINGLE_COLUMN, [(0.0, 0.0)], 1.0)

    pw = blocks[0].page_width
    ph = blocks[0].page_height

    # Step 1: tables win over multi-column. This is the critical fix.
    if _is_table_page(blocks, pw, ph):
        return LayoutProfile(PageLayoutType.TABLE_HEAVY, [(0.0, pw)], 0.75)

    # Step 2: detect genuine prose-column gutters
    gutters = _detect_columns(blocks, pw, ph)
    if not gutters:
        return LayoutProfile(PageLayoutType.SINGLE_COLUMN, [(0.0, pw)], 0.85)

    # Step 3: build column boundaries for downstream metadata/reporting
    boundaries: List[Tuple[float, float]] = []
    prev = 0.0
    for g in gutters:
        boundaries.append((prev, g))
        prev = g
    boundaries.append((prev, pw))

    # Step 4: COMPLEX_MIXED if any block spans across a gutter (full-width header)
    has_spanning = any(
        any(b.x0 < g < b.x1 for g in gutters) for b in blocks
    )
    if has_spanning:
        return LayoutProfile(PageLayoutType.COMPLEX_MIXED, boundaries, 0.8)
    return LayoutProfile(PageLayoutType.MULTI_COLUMN, boundaries, 0.9)
