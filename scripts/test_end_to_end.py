#!/usr/bin/env python3
"""End-to-end pipeline test: PDF -> Markdown -> RAG -> agent -> engine.

Compares computed dues against the published ground truth for the TNPA
tariff book (April 2024 - March 2025) applied to M/V SUDESTADA at Durban.

Run from project root:
    uv run python scripts/test_end_to_end.py
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

GROUND_TRUTH = {
    "LIGHT_DUES": 60062.04,
    "PORT_DUES": 199549.22,
    "TOWAGE_DUES": 147074.38,
    "VTS_DUES": 33315.75,
    "PILOTAGE_DUES": 47189.94,
    "RUNNING_LINES": 19639.50,
}

TOLERANCE_PCT = 0.5


def preflight() -> list[str]:
    """Return a list of fatal environment problems (empty == OK)."""
    problems: list[str] = []

    if not (Path(".env").is_file() or Path(".env.local").is_file()):
        problems.append(
            "no .env file found in project root; copy one of the .env.*.backup "
            "files to .env and set LLM_API_KEY"
        )

    # LLM key must be non-empty
    try:
        sys.path.insert(0, str(Path("src").resolve()))
        from port_tariff.config import get_settings
        settings = get_settings()
        if not settings.llm_api_key or settings.llm_api_key.startswith("your_"):
            problems.append(
                f"LLM_API_KEY is empty or placeholder (got {settings.llm_api_key[:6]!r}...)"
            )
    except Exception as e:
        problems.append(f"cannot load settings: {type(e).__name__}: {e}")

    # TEI embeddings service reachable?
    try:
        import httpx
        base = "http://localhost:8080/v1"
        try:
            from port_tariff.config import get_settings
            base = get_settings().embedding_api_base
        except Exception:
            pass
        try:
            r = httpx.get(base.replace("/v1", "") + "/health", timeout=3.0)
            if r.status_code >= 400:
                problems.append(
                    f"TEI embeddings endpoint {base} returned HTTP {r.status_code}; "
                    "start it with: docker compose up -d tei-embeddings"
                )
        except httpx.HTTPError:
            problems.append(
                f"TEI embeddings endpoint {base} unreachable; "
                "start it with: docker compose up -d tei-embeddings"
            )
    except ImportError:
        problems.append("httpx not installed; run `uv sync`")

    return problems


def run() -> int:
    problems = preflight()
    if problems:
        print("PREFLIGHT FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 2

    from port_tariff.models.vessel import VesselProfile
    from port_tariff.service import PortTariffService

    pdf = Path("data/port-tariff.pdf")
    vessel_path = Path("data/vessel_sudestada_durban.json")
    if not pdf.is_file():
        print(f"PDF not found: {pdf}")
        return 2
    if not vessel_path.is_file():
        print(f"vessel fixture not found: {vessel_path}")
        return 2

    vessel = VesselProfile.model_validate(json.loads(vessel_path.read_text()))

    print("=" * 72)
    print("End-to-End Pipeline Test")
    print("=" * 72)
    print(f"  PDF       : {pdf}")
    print(f"  Vessel    : {vessel.vessel_metadata.name} ({vessel.technical_specs.type}, "
          f"{vessel.technical_specs.gross_tonnage} GT)")
    print(f"  Port      : {vessel.port_name}")
    print(f"  Stay      : {vessel.billable_stay_days:.3f} days")
    print("=" * 72)

    try:
        service = PortTariffService()
        result = service.process_tariff_and_calculate(
            tariff_pdf_path=pdf,
            vessel_profile=vessel,
            force_reparse=True,   # bypass the MarkItDown-keyed cache
        )
    except Exception as e:
        print("\nPIPELINE EXCEPTION:")
        traceback.print_exc()
        return 1

    print()
    print("=" * 72)
    print("Results vs Ground Truth")
    print("=" * 72)
    print(f"  {'Category':<18} {'Computed':>14} {'Expected':>14} {'Delta':>8}  Status")
    print("  " + "-" * 66)

    seen = set()
    all_pass = True
    for item in result.items:
        cat = item.canonical_category
        seen.add(cat)
        actual = item.calculated_value
        expected = GROUND_TRUTH.get(cat)
        if expected is None:
            print(f"  {cat:<18} {actual:>14.2f} {'?':>14} {'?':>8}  (no ground truth)")
            continue
        delta_pct = abs(actual - expected) / expected * 100 if expected else 0.0
        ok = delta_pct <= TOLERANCE_PCT
        all_pass = all_pass and ok
        status = "PASS" if ok else "FAIL"
        print(f"  {cat:<18} {actual:>14.2f} {expected:>14.2f} "
              f"{delta_pct:>7.2f}%  {status}")

    missing = set(GROUND_TRUTH) - seen
    if missing:
        all_pass = False
        print()
        print(f"  MISSING categories (not emitted by agent): {sorted(missing)}")

    print("  " + "-" * 66)
    print(f"  Total computed : {result.total_dues:.2f} {result.currency}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")
    print()

    # Per-item clause + formula audit trail (useful when something fails)
    print("=" * 72)
    print("Audit Trail")
    print("=" * 72)
    for item in result.items:
        print(f"\n  [{item.canonical_category}] {item.item_name}")
        print(f"    source : {item.source_tariff_item_name}")
        print(f"    clause : {item.clause_reference}")
        print(f"    formula: {item.formula_breakdown}")
        print(f"    reason : {item.explanation}")

    print()
    print("=" * 72)
    print("RESULT:", "PASS" if all_pass else "FAIL")
    print("=" * 72)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(run())
