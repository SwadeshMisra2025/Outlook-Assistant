# Outlook Assistant - General Edition (****Convert the Outlook to Classic Mode as the first Step****)

## Quick Download & Run (Non-Developer)

1. Open this repository on GitHub.
2. Click **Code -> Download ZIP**.
3. Extract the ZIP fully.
4. Open `deployment/SETUP (run once).bat` and run it once.
5. During setup, the script will auto-detect your source `local_search.db` or ask for its path and then run the first SQLite load automatically.
6. Run `deployment/START Outlook Assistant.bat`.
7. App opens at `http://127.0.0.1:8010`.

If you are non-technical, follow this section first. Detailed setup remains below.

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
- `deployment/` - non-developer launchers (`SETUP (run once).bat`, `START Outlook Assistant.bat`)
- `docs/ITERATION_HISTORY.md` - full history of quick versions and improvements
- `docs/PYTHON_ENV_SETUP.md` - detailed Python setup for Windows

## Non-Developer Deployment (Recommended)

Use these steps if the user does not have a developer setup.

1. Open the GitHub repository and select **Code -> Download ZIP**.
2. Extract the ZIP completely to a local folder (for example `C:\Outlook-Assistant-main`).
3. Open the extracted folder and then open the `deployment` folder.
4. Double-click `SETUP (run once).bat`.
5. Wait until setup finishes. This step installs Python, build tools, Ollama, Python packages, pulls required Ollama models, configures `SOURCE_SQLITE_PATH`, and runs the initial SQLite load into the packaged local database.
6. If setup could not auto-detect your `local_search.db`, enter the path when prompted or set `backend/.env` later.
7. Double-click `START Outlook Assistant.bat`.
8. The app will open automatically at `http://127.0.0.1:8010`.

Notes:
- Run setup only once per machine.
- Use the start file every time you want to launch the app.
- Keep Outlook desktop installed and signed in before indexing.

## Quick Start (3 steps)

> **Prerequisites on Windows:**
> - [Python 3.12](https://python.org/downloads/) — tick "Add to PATH" during install
> - [Ollama](https://ollama.com) — needed for chat/semantic features (metrics/search still work without it)
> - Your Outlook SQLite export (`local_search.db`) from the Dev1 ingestion pipeline

### Step 1 — Clone and run setup (one time only)

```powershell
git clone <repo-url>
cd Aletha-One-General-Edition
.\setup.ps1
```

`setup.ps1` creates the Python virtual environment, installs all dependencies,
copies `.env.example` → `backend/.env`, tries to auto-detect `SOURCE_SQLITE_PATH`,
and runs the first incremental SQLite load into `backend/data/local_search.db`.
It also pulls the required Ollama models if Ollama is already installed.

### Step 2 — Point the app at your data

Open `backend/.env` and fill in:

```env
SOURCE_SQLITE_PATH=C:\path\to\your\local_search.db
```

Leave it blank if you're running locally alongside Dev1 (auto-detected).

If setup already detected your source SQLite and completed the initial load, you do not need to do anything else here.

### Step 3 — Start the app

```powershell
.\start.ps1
```

The script starts the backend, waits for it to be healthy, and automatically opens
`http://127.0.0.1:8010` in your browser. The **frontend is served from the same port**
— no separate server or extra steps.

| Endpoint | Purpose |
|---|---|
| `http://127.0.0.1:8010/` | Full UI (Workbench, Chat, Metrics, Architecture) |
| `http://127.0.0.1:8010/api/health` | Health check |
| `http://127.0.0.1:8010/api/metrics?top_n=10` | Metrics JSON |
| `http://127.0.0.1:8010/docs` | Interactive API docs (Swagger UI) |

### Sharing with a colleague

1. Share this repo (zip or `git clone`).
2. Colleague runs `setup.ps1` → edits `backend/.env` → runs `start.ps1`.
3. That's it — no Node.js, no Docker, no build step required.

See `docs/PYTHON_ENV_SETUP.md` for troubleshooting venv and Ollama issues.

## Next Phase Backlog

1. Implement direct historical backfill from Outlook and Graph into Chroma.
2. Add per-message lineage IDs and chunk audit logs.
3. Add SQL adapters for counts and trend analytics.
4. Add hybrid orchestrator with parallel execution and fact-locking.
5. Add benchmark suite for recall and answer-grounding quality.
