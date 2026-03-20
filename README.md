# Outlook Assistant - General Edition

First Product (General Edition) foundation for the next phase.

This repository is a clean starting point for:
- Direct-source semantic ingestion (Outlook/Graph to chunks/vectors)
- Deterministic SQL analytics for counts and numeric facts
- Hybrid answer synthesis for mixed analytical + narrative queries
- Productized UI with a refreshed visual direction

## Product Direction

### Core principles
- Semantic retrieval and SQL analytics are separate but composable.
- Numeric facts come from SQL (authoritative).
- Summaries come from Chroma evidence (grounded).
- Mixed questions run both paths in parallel, then synthesize.

### Target architecture
1. Ingest from source systems (Outlook COM, Graph).
2. Build chunked semantic memory in Chroma directly from source payloads.
3. Persist normalized facts for analytics in SQLite.
4. Route query as `sql`, `semantic`, or `hybrid`.
5. Return one response with evidence + numeric facts.

## Project Layout

- `backend/` - FastAPI service skeleton for routing and orchestration
- `frontend/` - Product UI starter with refreshed General Edition look
- `docs/ITERATION_HISTORY.md` - full history of quick versions and improvements
- `docs/PYTHON_ENV_SETUP.md` - detailed Python setup for Windows

## Quick Start

### Backend
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8010 --reload
```

Health check: `http://127.0.0.1:8010/api/health`

Metrics endpoint: `http://127.0.0.1:8010/api/metrics?top_n=10`

### Frontend
Open `frontend/index.html` directly in browser for Phase-1 UI prototype.

## Next Phase Backlog

1. Implement direct historical backfill from Outlook and Graph into Chroma.
2. Add per-message lineage IDs and chunk audit logs.
3. Add SQL adapters for counts and trend analytics.
4. Add hybrid orchestrator with parallel execution and fact-locking.
5. Add benchmark suite for recall and answer-grounding quality.
