# Autonomous Agentic Port Tariff Calculation Engine

An autonomous intelligence engine that consumes complex Port Authority Tariff PDFs and vessel queries to extract statutory rules and deterministically calculate all applicable port dues.

## Architecture

```
[Tariff PDF] ──► Layout-Aware Converter (pdfminer + pdfplumber + XY-Cut)
                         │
                         ▼
              Table-Aware Markdown Splitter (clause-boundary aware)
                         │
                         ▼
              ChromaDB Vector Index (BGE-M3 via HuggingFace TEI)
                         │
                         ▼
              Charge Discovery Agent ──► Charge Catalog (LLM-discovered, cached)
                         │
                         ▼
              Catalog-Driven Retrieval (section refs + generated queries)
                         │
                         ▼
              Per-Charge Extraction Agents (concurrent, isolated failures)
                         │
                         ▼
              Deterministic Calculation Engine (Decimal math, no LLM arithmetic)
                         │
                         ▼
              [Itemized Disbursement with Audit Trail]
```

## Benchmark (TNPA Tariff 2024–2025, M/V SUDESTADA at Durban)

| Tariff Item | Ground Truth (ZAR) | Engine (ZAR) | Accuracy |
|---|---|---|---|
| Light Dues | 60,062.04 | 60,062.04 | 100.00% |
| Port Dues | 199,549.22 | 199,549.22 | 100.00% |
| Towage Dues | 147,074.38 | 147,074.38 | 100.00% |
| Pilotage Dues | 47,189.94 | 47,189.94 | 100.00% |
| Running Lines | 19,639.50 | 19,639.50 | 100.00% |
| VTS Dues | 33,315.75 | 33,345.00 | 99.91% |

*VTS variance (0.08%): ground truth uses registry GT 51,255; input uses 51,300.*
*Port dues ground truth implies 3.396 billable days (unrounded alongside hours); input provides 3.39.*

## Quickstart

### Prerequisites
- Python 3.11+, [uv](https://docs.astral.sh/uv/)
- Docker & Docker Compose (for TEI embeddings)

### Setup

```bash
git clone <repo-url> && cd port-tariff
cp .env.example .env   # add your LLM_API_KEY
uv sync --group dev
docker compose up -d tei-embeddings
uv run uvicorn port_tariff.main:app --host 0.0.0.0 --port 8000 --reload
```

### Calculate Dues

```bash
curl -X POST http://localhost:8000/api/v1/calculate \
  -F "tariff_pdf=@data/port-tariff.pdf" \
  -F 'vessel_data={"port_name":"Durban","vessel_metadata":{"name":"SUDESTADA"},"technical_specs":{"type":"Bulk Carrier","dwt":93274,"gross_tonnage":51300,"loa_meters":229.2,"beam_meters":38.0},"operational_data":{"cargo_quantity_mt":40000,"days_alongside":3.39,"num_operations":2}}' | jq .
```

Natural-language queries are also accepted in `vessel_data`.

### Run Tests

```bash
uv run pytest -v                          # unit tests (no LLM needed)
uv run python scripts/test_end_to_end.py  # full pipeline (requires .env + TEI)
```

## Production Deployment

```bash
docker compose up --build -d
```

*Made with ❤️ by Vlad.*
