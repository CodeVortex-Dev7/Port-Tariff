#!/usr/bin/env python3
"""General smoke test for the layout-aware PDF converter.

This test makes NO assertions about specific port names, section numbers,
or tariff rates. It verifies structural properties that hold for any
well-formed multi-column regulatory document:

  1. Output is non-trivial in size.
  2. Numbered clause headings are present in significant quantity.
  3. Heading numbers appear in monotonically increasing document order
     (with tolerance for the occasional cross-reference).
  4. At least a few Markdown tables are present.

Run:  uv run python scripts/smoke_test_layout.py <pdf_path>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _parse_clause_number(s: str) -> tuple[int, ...]:
    return tuple(int(p) for p in s.split("."))


def run_smoke_test(pdf_path: str) -> tuple[bool, list[str]]:
    """Return (passed, messages). Never raises."""
    messages: list[str] = []

    try:
        from port_tariff.ingestion.layout_aware_converter import (
            LayoutAwarePDFConverter,
        )
    except ImportError as e:
        return False, [f"import failed: {e}"]

    try:
        md = LayoutAwarePDFConverter().convert_to_markdown(pdf_path)
    except Exception as e:
        return False, [f"conversion error: {type(e).__name__}: {e}"]

    messages.append(f"output_chars={len(md)}")

    # Check 1: non-trivial output size (a well-formed tariff doc is large).
    if len(md) < 5000:
        messages.append(f"FAIL: output too short ({len(md)} chars)")
        return False, messages

    # Check 2: numbered clause headings present in quantity.
    # Matches "1.1", "4.1.1", "6.2.2" followed by a capitalised word.
    # Language-agnostic: relies on the numeration convention shared by
    # regulatory documents across jurisdictions, not on English keywords.
    heading_pattern = re.compile(
        r"^\s{0,8}(\d+(?:\.\d+){1,3})\s+[A-Z]", re.MULTILINE
    )
    headings = [(m.group(1), m.start()) for m in heading_pattern.finditer(md)]
    messages.append(f"numbered_headings={len(headings)}")
    if len(headings) < 10:
        messages.append(
            f"FAIL: too few numbered headings ({len(headings)}); "
            "content may have been dropped"
        )
        return False, messages

    # Check 3: heading numbers appear in document order.
    # Compare consecutive headings within the SAME top-level section to
    # avoid flagging legitimate cross-references (e.g. clause 4 referring
    # back to clause 3).
    inversions = 0
    comparisons = 0
    for i in range(len(headings) - 1):
        a = _parse_clause_number(headings[i][0])
        b = _parse_clause_number(headings[i + 1][0])
        if a[0] != b[0]:
            continue  # top-level section change; skip
        comparisons += 1
        if a > b:
            inversions += 1
    messages.append(f"in_section_comparisons={comparisons} inversions={inversions}")
    if comparisons > 0 and inversions > comparisons * 0.15:
        messages.append(
            f"FAIL: excessive in-section heading order inversions "
            f"({inversions}/{comparisons}); reading order may be scrambled"
        )
        return False, messages

    # Check 4: Markdown tables present (regulatory docs contain rate tables).
    md_table_separators = md.count("| ---") + md.count("|---")
    messages.append(f"markdown_tables={md_table_separators}")
    if md_table_separators < 3:
        messages.append(
            f"FAIL: too few Markdown tables ({md_table_separators}); "
            "ruled tables may not be rendered"
        )
        return False, messages

    return True, messages


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: smoke_test_layout.py <pdf_path>")
        return 2
    pdf = sys.argv[1]
    if not Path(pdf).is_file():
        print(f"PDF not found: {pdf}")
        return 2
    passed, msgs = run_smoke_test(pdf)
    for m in msgs:
        print(f"  {m}")
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
