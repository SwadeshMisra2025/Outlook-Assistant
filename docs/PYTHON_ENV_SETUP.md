# Python Environment Setup (Windows)

This guide sets up a stable backend environment for Outlook Assistant - General Edition.

## Recommended Versions

- Python: 3.12.x
- Pip: latest within venv
- Optional model runtime: Ollama (local)
- Compiler tools on Windows: Visual Studio C++ Build Tools

## Why Python 3.12

For this stack (FastAPI + Chroma ecosystem), Python 3.12 generally has fewer binary build issues on Windows than newer versions.

## Prerequisites

1. Install Python 3.12 (and ensure launcher works).
2. Install Visual Studio Build Tools with C++ workload.
3. Install Ollama if semantic embedding/generation is needed.

## Create and Activate Virtual Environment

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
```

If PowerShell blocks activation, run once as admin:

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Install Dependencies

```powershell
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## Configure Environment Variables

1. Copy `backend/.env.example` to `backend/.env`.
2. Update values as needed:
- `OLLAMA_BASE_URL`
- `EMBED_MODEL`
- `CHAT_MODEL`
- `SQLITE_PATH`
- `CHROMA_DIR`

## Run API

```powershell
python -m uvicorn app.main:app --port 8010 --reload
```

Check: `http://127.0.0.1:8010/api/health`

## Optional: Ollama Models

```powershell
ollama pull nomic-embed-text
ollama pull mistral
```

## Troubleshooting

### `pip` build or wheel errors
1. Confirm Python 3.12 is active.
2. Recreate venv after version change.
3. Ensure C++ build tools installed.

### Activation script blocked
- Use execution policy command shown above.

### Port conflict
- Change API port in uvicorn command or stop conflicting process.

### Wrong interpreter in VS Code
1. Use Command Palette -> Python: Select Interpreter.
2. Pick `backend/.venv/Scripts/python.exe`.

## Reproducibility Tips

1. Pin dependencies in `requirements.txt`.
2. Keep `.env.example` in sync with required keys.
3. Capture environment diagnostics for onboarding:

```powershell
python --version
pip --version
pip freeze > requirements.lock.txt
```
