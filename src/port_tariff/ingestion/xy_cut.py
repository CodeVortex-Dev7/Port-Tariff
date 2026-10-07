"""Recursive XY-Cut reading order reconstruction.

Defence in depth: even when a page is classified as MULTI_COLUMN or
COMPLEX_MIXED, XY-Cut refuses a vertical cut unless both resulting
regions have substantial vertical extent. This prevents shredding wide
table rows that were misclassified upstream.
"""

from typing import List, Optional, Tuple

from port_tariff.ingestion.layout_extractor import TextBlock


def _vertical_cut(
    blocks: List[TextBlock],
    indices: List[int],
    min_gutter_ratio: float = 0.03,
    min_extent_ratio: float = 0.30,
) -> Optional[Tuple[float, float]]:
    """Find widest valid vertical gutter.

    Rejects the cut unless BOTH sides contain >= 2 blocks and span at
    least `min_extent_ratio` of the region's vertical extent.
    """
    if len(indices) < 4:
        return None

    intervals = sorted(
        ((blocks[i].x0, blocks[i].x1) for i in indices), key=lambda iv: iv[0]
    )
    merged: List[List[float]] = [[intervals[0][0], intervals[0][1]]]
    for x0, x1 in intervals[1:]:
        if x0 <= merged[-1][1] + 1.0:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    if len(merged) < 2:
        return None

    best_gap, best_cut = 0.0, 0.0
    for i in range(len(merged) - 1):
        gap = merged[i + 1][0] - merged[i][1]
        if gap > best_gap:
            best_gap, best_cut = gap, (merged[i][1] + merged[i + 1][0]) / 2.0

    region_w = (
        max(blocks[i].x1 for i in indices) - min(blocks[i].x0 for i in indices)
    )
    if region_w <= 0 or best_gap < region_w * min_gutter_ratio:
        return None

    left = [i for i in indices if blocks[i].x_center < best_cut]
    right = [i for i in indices if blocks[i].x_center >= best_cut]
    if len(left) < 2 or len(right) < 2:
        return None

    region_h = (
        max(blocks[i].y1 for i in indices) - min(blocks[i].y0 for i in indices)
    )
    if region_h <= 0:
        return None
    left_h = max(blocks[i].y1 for i in left) - min(blocks[i].y0 for i in left)
    right_h = max(blocks[i].y1 for i in right) - min(blocks[i].y0 for i in right)
    if left_h < region_h * min_extent_ratio or right_h < region_h * min_extent_ratio:
        return None

    return best_cut, best_gap


def _horizontal_cut(
    blocks: List[TextBlock],
    indices: List[int],
    min_gutter_ratio: float = 0.05,
) -> Optional[Tuple[float, float]]:
    if len(indices) < 2:
        return None

    intervals = sorted(
        ((blocks[i].y0, blocks[i].y1) for i in indices), key=lambda iv: iv[0]
    )
    merged: List[List[float]] = [[intervals[0][0], intervals[0][1]]]
    for y0, y1 in intervals[1:]:
        if y0 <= merged[-1][1] + 1.0:
            merged[-1][1] = max(merged[-1][1], y1)
        else:
            merged.append([y0, y1])
    if len(merged) < 2:
        return None

    best_gap, best_cut = 0.0, 0.0
    for i in range(len(merged) - 1):
        gap = merged[i + 1][0] - merged[i][1]
        if gap > best_gap:
            best_gap, best_cut = gap, (merged[i][1] + merged[i + 1][0]) / 2.0

    region_h = (
        max(blocks[i].y1 for i in indices) - min(blocks[i].y0 for i in indices)
    )
    if region_h <= 0 or best_gap < region_h * min_gutter_ratio:
        return None
    return best_cut, best_gap


def xy_cut_reading_order(
    blocks: List[TextBlock],
    indices: Optional[List[int]] = None,
) -> List[int]:
    """Recursive XY-Cut reading order. Returns block indices in reading order."""
    if indices is None:
        indices = list(range(len(blocks)))
    if len(indices) <= 1:
        return indices

    v = _vertical_cut(blocks, indices)
    if v is not None:
        cut_x = v[0]
        left = [i for i in indices if blocks[i].x_center < cut_x]
        right = [i for i in indices if blocks[i].x_center >= cut_x]
        if left and right:
            return xy_cut_reading_order(blocks, left) + xy_cut_reading_order(blocks, right)

    h = _horizontal_cut(blocks, indices)
    if h is not None:
        cut_y = h[0]
        top = [i for i in indices if blocks[i].y0 >= cut_y]
        bottom = [i for i in indices if blocks[i].y0 < cut_y]
        if top and bottom:
            return xy_cut_reading_order(blocks, top) + xy_cut_reading_order(blocks, bottom)

    return sorted(indices, key=lambda i: (-blocks[i].y1, blocks[i].x0))
