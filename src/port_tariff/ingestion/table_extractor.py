"""Extract ruled-border tables from PDF pages as Markdown via pdfplumber.

Two entry points:

  - ``find_tables_with_bbox`` returns [(bbox, markdown), ...] so the
    caller can merge table content with surrounding prose at the correct
    y-position. bbox is pdfplumber's (x0, top, x1, bottom) tuple in
    top-left-origin coordinates.

  - ``extract_tables_as_markdown`` is a convenience wrapper returning
    just the concatenated Markdown.

Degenerate results (single-row, single-column, or extremely narrow) are
filtered out - pdfplumber occasionally returns a "table" around a stray
horizontal rule on prose pages, and those artefacts harm more than help.
"""

from typing import List, Tuple

import pdfplumber

# Minimum structural requirements for a pdfplumber result to count as a
# real table. Tuned to reject horizontal rules and single-row artefacts
# while keeping small legitimate rate tables intact.
_MIN_ROWS = 2
_MIN_COLS = 2
_MIN_PAGE_WIDTH_FRACTION = 0.15


def _is_valid_table(data: List[List], bbox, page_width: float) -> bool:
    if not data or len(data) < _MIN_ROWS:
        return False
    max_cols = max((len(row) for row in data if row), default=0)
    if max_cols < _MIN_COLS:
        return False
    x0, _, x1, _ = bbox
    if (x1 - x0) < page_width * _MIN_PAGE_WIDTH_FRACTION:
        return False
    return True


def _table_to_markdown(table: List[List[str]]) -> str:
    """Convert a list-of-lists to GitHub-flavoured Markdown."""
    if not table or len(table) < 2:
        return ""
    # Preserve intra-cell line breaks as <br>. Merged header/data cells
    # (e.g. a "Basic fee\nPer 100 tons" label paired with a "3175.89\n13.46"
    # data cell) lose structural meaning when collapsed to spaces, and the
    # LLM cannot disambiguate two adjacent values from a single string.
    header = [str(c or "").replace("\n", "<br>").strip() for c in table[0]]
    if not header or not any(header):
        return ""
    sep = "| " + " | ".join("---" for _ in header) + " |"
    rows = []
    for row in table[1:]:
        cells = [
            str(c or "").replace("\n", "<br>").strip() for c in row[: len(header)]
        ]
        cells = (cells + [""] * len(header))[: len(header)]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(["| " + " | ".join(header) + " |", sep] + rows)


def find_tables_with_bbox(
    pdf_path: str, page_num: int
) -> List[Tuple[Tuple[float, float, float, float], str]]:
    """Return [(bbox, markdown), ...] for tables on a 0-indexed page.

    bbox is (x0, top, x1, bottom) in pdfplumber's top-left-origin coords.
    """
    results: List[Tuple[Tuple[float, float, float, float], str]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        if page_num >= len(pdf.pages):
            return results
        page = pdf.pages[page_num]
        page_width = float(page.width)
        try:
            tables = page.find_tables(
                {
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                }
            )
        except Exception:
            return results

        for t in tables:
            try:
                data = t.extract()
            except Exception:
                continue
            if not _is_valid_table(data or [], t.bbox, page_width):
                continue
            md = _table_to_markdown(data or [])
            if md:
                results.append((t.bbox, md))
    return results


def extract_tables_as_markdown(pdf_path: str, page_num: int) -> str:
    """Convenience wrapper returning just the concatenated table Markdown."""
    return "\n\n".join(md for _, md in find_tables_with_bbox(pdf_path, page_num))
