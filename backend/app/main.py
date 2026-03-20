from datetime import datetime, timezone
import os
import sqlite3
from typing import Any
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    top_k: int = Field(default=6, ge=1, le=25)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None


class CompletenessFeedbackRequest(BaseModel):
    query_id: str = Field(..., min_length=3)
    score: int = Field(..., ge=1, le=5)
    comment: str = ""


class AdminLoadRequest(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"


app = FastAPI(
    title="Outlook Assistant - General Edition API",
    version="0.1.0",
    description="Foundation API for hybrid SQL + semantic search architecture.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3001",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db_path() -> str:
    return os.getenv("SQLITE_PATH", "./data/local_search.db")


def _source_db_path() -> str | None:
    explicit = os.getenv("SOURCE_SQLITE_PATH")
    if explicit and os.path.exists(explicit):
        return explicit

    cwd = Path.cwd()
    candidates = [
        cwd / ".." / ".." / "Dev1" / "backend" / "local_search.db",
        cwd / ".." / ".." / ".." / "Dev1" / "backend" / "local_search.db",
    ]
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    return None


def _tracking_db_path() -> str:
    return os.getenv("TRACKING_DB_PATH", "./data/query_tracking.db")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _tracking_conn() -> sqlite3.Connection:
    path = _tracking_db_path()
    _ensure_parent_dir(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_tracking_tables() -> None:
    conn = _tracking_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_queries (
                query_id TEXT PRIMARY KEY,
                query_text TEXT NOT NULL,
                top_k INTEGER NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_load_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                source_db TEXT,
                target_db TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _record_admin_run(mode: str, source_db: str | None, target_db: str, status: str, details_json: str) -> None:
    conn = _tracking_conn()
    try:
        conn.execute(
            """
            INSERT INTO admin_load_runs (mode, source_db, target_db, status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (mode, source_db, target_db, status, details_json, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _list_source_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _ensure_target_table_from_source(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection, table_name: str) -> None:
    row = src_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not row or not row[0]:
        return
    dst_conn.execute(row[0])


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(r[1]) for r in rows]


def _copy_full_table(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection, table_name: str) -> int:
    dst_conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    _ensure_target_table_from_source(src_conn, dst_conn, table_name)
    cols = _table_columns(src_conn, table_name)
    if not cols:
        return 0
    col_csv = ", ".join(cols)
    ph = ", ".join(["?" for _ in cols])
    rows = src_conn.execute(f"SELECT {col_csv} FROM {table_name}").fetchall()
    if rows:
        dst_conn.executemany(
            f"INSERT INTO {table_name} ({col_csv}) VALUES ({ph})",
            rows,
        )
    return len(rows)


def _copy_incremental_table(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection, table_name: str) -> tuple[int, str]:
    _ensure_target_table_from_source(src_conn, dst_conn, table_name)
    cols = _table_columns(src_conn, table_name)
    if not cols:
        return 0, "no_columns"

    col_csv = ", ".join(cols)
    ph = ", ".join(["?" for _ in cols])
    lower = {c.lower() for c in cols}

    if "id" in lower:
        id_col = next(c for c in cols if c.lower() == "id")
        max_target = dst_conn.execute(f"SELECT COALESCE(MAX({id_col}), 0) FROM {table_name}").fetchone()[0]
        rows = src_conn.execute(
            f"SELECT {col_csv} FROM {table_name} WHERE {id_col} > ? ORDER BY {id_col}",
            (max_target,),
        ).fetchall()
        if rows:
            dst_conn.executemany(
                f"INSERT INTO {table_name} ({col_csv}) VALUES ({ph})",
                rows,
            )
        return len(rows), f"id>{max_target}"

    ts_candidates = ["received_at", "sent_at", "created_at", "start_time", "start"]
    ts_col = next((c for c in cols if c.lower() in ts_candidates), None)
    if ts_col:
        max_target = dst_conn.execute(f"SELECT COALESCE(MAX({ts_col}), '') FROM {table_name}").fetchone()[0]
        rows = src_conn.execute(
            f"SELECT {col_csv} FROM {table_name} WHERE {ts_col} > ? ORDER BY {ts_col}",
            (max_target,),
        ).fetchall()
        if rows:
            dst_conn.executemany(
                f"INSERT INTO {table_name} ({col_csv}) VALUES ({ph})",
                rows,
            )
        return len(rows), f"{ts_col}>{max_target}"

    return 0, "no_incremental_key"


def _run_admin_load(mode: str) -> dict[str, Any]:
    source_path = _source_db_path()
    target_path = _db_path()
    _ensure_parent_dir(target_path)

    if not source_path:
        return {
            "status": "error",
            "message": "No source SQLite found. Set SOURCE_SQLITE_PATH or keep Dev1 backend DB available.",
            "source_db": None,
            "target_db": target_path,
            "tables": [],
        }

    src_conn = sqlite3.connect(source_path)
    dst_conn = sqlite3.connect(target_path)

    try:
        src_tables = set(_list_source_tables(src_conn))
        preferred = [
            "emails",
            "meetings",
            "teams_messages",
            "teams_channel_messages",
            "email_attachments",
        ]
        tables = [t for t in preferred if t in src_tables]

        details = []
        for t in tables:
            if mode == "full":
                inserted = _copy_full_table(src_conn, dst_conn, t)
                details.append({"table": t, "inserted": inserted, "strategy": "replace_all"})
            else:
                inserted, key = _copy_incremental_table(src_conn, dst_conn, t)
                details.append({"table": t, "inserted": inserted, "strategy": key})

        dst_conn.commit()

        return {
            "status": "ok",
            "mode": mode,
            "source_db": source_path,
            "target_db": target_path,
            "tables": details,
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        src_conn.close()
        dst_conn.close()


def _log_semantic_query(query_id: str, query_text: str, top_k: int, mode: str) -> None:
    conn = _tracking_conn()
    try:
        conn.execute(
            "INSERT INTO semantic_queries (query_id, query_text, top_k, mode, created_at) VALUES (?, ?, ?, ?, ?)",
            (query_id, query_text, top_k, mode, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _log_chat_message(session_id: str, role: str, content: str) -> None:
    conn = _tracking_conn()
    try:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _recent_chat(session_id: str, limit: int = 8) -> list[dict[str, str]]:
    conn = _tracking_conn()
    try:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _first_existing_column(conn: sqlite3.Connection, table_name: str, candidates: list[str]) -> str | None:
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {str(c[1]).lower() for c in cols}
    for c in candidates:
        if c.lower() in existing:
            return c
    return None


def _split_people(raw: str | None) -> list[str]:
    if not raw:
        return []
    normalized = raw.replace(";", ",")
    parts = [p.strip().lower() for p in normalized.split(",") if p.strip()]
    deduped = []
    seen = set()
    for p in parts:
        if p not in seen:
            deduped.append(p)
            seen.add(p)
    return deduped


def _demo_metrics() -> dict[str, Any]:
    return {
        "source": "demo",
        "emails_by_sender": [
            {"sender": "garvin@example.com", "count": 42},
            {"sender": "nina@example.com", "count": 31},
            {"sender": "ravi@example.com", "count": 21},
            {"sender": "alex@example.com", "count": 18},
            {"sender": "maria@example.com", "count": 14},
        ],
        "email_participant_mix": {"one_to_one": 86, "group": 54},
        "meeting_participant_mix": {"one_to_one": 37, "group": 49},
        "meta": {
            "note": "Demo metrics because SQLite database was not found.",
        },
    }


def _compute_metrics(top_n: int = 10) -> dict[str, Any]:
    path = _db_path()
    if not os.path.exists(path):
        data = _demo_metrics()
        data["meta"]["sqlite_path"] = path
        return data

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    try:
        result: dict[str, Any] = {
            "source": "sqlite",
            "emails_by_sender": [],
            "email_participant_mix": {"one_to_one": 0, "group": 0},
            "meeting_participant_mix": {"one_to_one": 0, "group": 0},
            "meta": {"sqlite_path": path, "warnings": []},
        }

        if _table_exists(conn, "emails"):
            sender_col = _first_existing_column(conn, "emails", ["sender", "sender_email", "from_email", "from_address"])
            recipient_col = _first_existing_column(conn, "emails", ["recipients", "to_recipients", "to_emails", "participant_emails"])

            if sender_col:
                rows = conn.execute(
                    f"SELECT COALESCE({sender_col}, '') AS sender, COUNT(*) AS c FROM emails GROUP BY sender ORDER BY c DESC LIMIT ?",
                    (top_n,),
                ).fetchall()
                result["emails_by_sender"] = [
                    {"sender": (r["sender"] or "unknown"), "count": int(r["c"])} for r in rows
                ]
            else:
                result["meta"]["warnings"].append("No sender-like column found in emails table.")

            if recipient_col:
                rows = conn.execute(f"SELECT {recipient_col} AS recipients FROM emails").fetchall()
                one = 0
                group = 0
                for r in rows:
                    recipients = _split_people(r["recipients"])
                    if len(recipients) <= 1:
                        one += 1
                    else:
                        group += 1
                result["email_participant_mix"] = {"one_to_one": one, "group": group}
            else:
                result["meta"]["warnings"].append("No recipient-like column found in emails table.")
        else:
            result["meta"]["warnings"].append("Emails table not found.")

        if _table_exists(conn, "meetings"):
            attendees_col = _first_existing_column(
                conn,
                "meetings",
                ["attendees", "participants", "participant_emails", "attendee_emails"],
            )

            if attendees_col:
                rows = conn.execute(f"SELECT {attendees_col} AS attendees FROM meetings").fetchall()
                one = 0
                group = 0
                for r in rows:
                    attendees = _split_people(r["attendees"])
                    if len(attendees) <= 1:
                        one += 1
                    else:
                        group += 1
                result["meeting_participant_mix"] = {"one_to_one": one, "group": group}
            else:
                result["meta"]["warnings"].append("No attendee-like column found in meetings table.")
        else:
            result["meta"]["warnings"].append("Meetings table not found.")

        return result
    finally:
        conn.close()


def _completeness_metrics() -> dict[str, Any]:
    conn = _tracking_conn()
    try:
        rows = conn.execute(
            "SELECT score, COUNT(*) AS c FROM query_feedback GROUP BY score ORDER BY score"
        ).fetchall()
        distribution = {str(i): 0 for i in range(1, 6)}
        total = 0
        weighted = 0
        for r in rows:
            score = int(r["score"])
            count = int(r["c"])
            distribution[str(score)] = count
            total += count
            weighted += score * count

        avg = round(weighted / total, 2) if total else None

        recent = conn.execute(
            """
            SELECT qf.query_id, qf.score, qf.comment, qf.created_at, sq.query_text
            FROM query_feedback qf
            LEFT JOIN semantic_queries sq ON sq.query_id = qf.query_id
            ORDER BY qf.id DESC
            LIMIT 15
            """
        ).fetchall()

        return {
            "distribution": distribution,
            "average_score": avg,
            "total_feedback": total,
            "recent_feedback": [
                {
                    "query_id": r["query_id"],
                    "query": r["query_text"],
                    "score": r["score"],
                    "comment": r["comment"],
                    "created_at": r["created_at"],
                }
                for r in recent
            ],
        }
    finally:
        conn.close()


@app.on_event("startup")
def _startup() -> None:
    _init_tracking_tables()


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "outlook-assistant-general-edition",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/architecture")
def architecture() -> dict[str, Any]:
    return {
        "name": "Outlook Assistant - General Edition",
        "version": "0.1.0",
        "flow": [
            "Source ingestion (Outlook/Graph)",
            "SQL facts lane (counts, deterministic analytics)",
            "Semantic lane (chunk + embed + vector retrieval)",
            "Hybrid orchestration (parallel SQL + semantic)",
            "Grounded synthesis with citations + numeric facts",
            "Feedback loop (completeness scoring + query tracking)",
        ],
        "components": {
            "api": {
                "health": "/api/health",
                "search": "/api/search",
                "chat": "/api/chat/message",
                "metrics": "/api/metrics",
                "completeness": "/api/metrics/completeness",
            },
            "data": {
                "sql_path": _db_path(),
                "tracking_path": _tracking_db_path(),
            },
        },
        "modes": ["sql", "semantic", "hybrid"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/technology-map")
def technology_map() -> dict[str, Any]:
    return {
        "title": "Outlook Assistant Technology Flow",
        "objective": "Help local builders understand architecture, stack choices, and high-impact improvement areas.",
        "stages": [
            {
                "name": "1) Ingestion",
                "purpose": "Pull raw activity from Outlook/Graph into local processing lanes.",
                "technologies": ["Outlook COM", "Microsoft Graph", "Python connectors"],
                "current_state": "Planned in this repo scaffold; implemented in prior prototype iterations.",
                "improvements": [
                    "Add incremental sync using watermarks.",
                    "Capture attachment text extraction pipeline.",
                    "Track per-source ingestion failure queue.",
                ],
            },
            {
                "name": "2) SQL Facts Lane",
                "purpose": "Answer deterministic count and numeric analytics queries.",
                "technologies": ["SQLite", "SQL aggregations", "FastAPI"],
                "current_state": "Metrics endpoint supports sender/participant analytics patterns.",
                "improvements": [
                    "Add schema migration tooling.",
                    "Add reusable query templates for common business questions.",
                    "Add metric cache for expensive aggregations.",
                ],
            },
            {
                "name": "3) Semantic Lane",
                "purpose": "Retrieve context-rich evidence for narrative and exploratory questions.",
                "technologies": ["Chunking", "Embeddings", "ChromaDB", "Ollama"],
                "current_state": "Designed as target lane; placeholder in current scaffold search output.",
                "improvements": [
                    "Implement direct-source historical chunk backfill.",
                    "Add retrieval evaluation set (precision, recall, coverage).",
                    "Add metadata filters for people, date, project.",
                ],
            },
            {
                "name": "4) Orchestration",
                "purpose": "Select SQL/semantic/hybrid and merge outputs into one grounded answer.",
                "technologies": ["FastAPI routing", "Execution planner", "Hybrid synthesis"],
                "current_state": "Mode routing placeholder currently implemented.",
                "improvements": [
                    "Parallelize SQL and semantic fetch in hybrid mode.",
                    "Lock numeric facts from SQL before generation.",
                    "Return confidence and citation coverage score.",
                ],
            },
            {
                "name": "5) Conversation and Feedback",
                "purpose": "Persist user sessions and measure answer completeness.",
                "technologies": ["Tracking SQLite", "Session history", "Feedback analytics"],
                "current_state": "Chat message logging and completeness scoring are implemented.",
                "improvements": [
                    "Turn low completeness into automatic follow-up retrieval.",
                    "Add per-user quality trend dashboards.",
                    "Add feedback labels (missing source, wrong count, stale context).",
                ],
            },
        ],
        "local_build_path": [
            "Run backend (FastAPI + uvicorn)",
            "Serve frontend static UI",
            "Use Technology tab to inspect architecture and staged improvements",
            "Implement one stage at a time and validate through metrics/completeness",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/admin/load-status")
def admin_load_status() -> dict[str, Any]:
    conn = _tracking_conn()
    try:
        rows = conn.execute(
            "SELECT id, mode, source_db, target_db, status, details_json, created_at FROM admin_load_runs ORDER BY id DESC LIMIT 10"
        ).fetchall()
        return {
            "source_db_detected": _source_db_path(),
            "target_db": _db_path(),
            "recent_runs": [
                {
                    "id": r["id"],
                    "mode": r["mode"],
                    "source_db": r["source_db"],
                    "target_db": r["target_db"],
                    "status": r["status"],
                    "details_json": r["details_json"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


@app.post("/api/admin/load")
def admin_load(req: AdminLoadRequest) -> dict[str, Any]:
    payload = _run_admin_load(mode=req.mode)
    _record_admin_run(
        mode=req.mode,
        source_db=payload.get("source_db"),
        target_db=payload.get("target_db", _db_path()),
        status=payload.get("status", "unknown"),
        details_json=str(payload),
    )
    return payload


@app.get("/api/metrics")
def metrics(top_n: int = 10) -> dict[str, Any]:
    payload = _compute_metrics(top_n=top_n)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@app.get("/api/metrics/completeness")
def metrics_completeness() -> dict[str, Any]:
    payload = _completeness_metrics()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@app.post("/api/search")
def search(req: SearchRequest) -> dict:
    # Phase-1 placeholder: return request echo and route hint.
    route_hint = "hybrid" if any(w in req.query.lower() for w in ["count", "how many", "total"]) else "semantic"
    query_id = str(uuid.uuid4())
    _log_semantic_query(query_id=query_id, query_text=req.query, top_k=req.top_k, mode=route_hint)
    return {
        "answer": "Search pipeline scaffold is active. Connect SQL and Chroma adapters next.",
        "mode": route_hint,
        "results": [],
        "metadata": {
            "query_id": query_id,
            "query": req.query,
            "top_k": req.top_k,
            "execution_path": ["router", "sql_or_semantic", "synthesis"],
        },
    }


@app.post("/api/feedback/completeness")
def feedback_completeness(req: CompletenessFeedbackRequest) -> dict[str, Any]:
    conn = _tracking_conn()
    try:
        conn.execute(
            "INSERT INTO query_feedback (query_id, score, comment, created_at) VALUES (?, ?, ?, ?)",
            (req.query_id, req.score, req.comment.strip(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "saved",
        "query_id": req.query_id,
        "score": req.score,
    }


@app.post("/api/chat/message")
def chat_message(req: ChatRequest) -> dict[str, Any]:
    session_id = req.session_id or str(uuid.uuid4())
    _log_chat_message(session_id, "user", req.message)

    recent = _recent_chat(session_id, limit=6)
    assistant_text = (
        "I am tracking this as a conversational thread. "
        "For production mode, connect this endpoint to the hybrid SQL + Chroma orchestrator so each turn can cite evidence and numeric facts."
    )
    _log_chat_message(session_id, "assistant", assistant_text)

    return {
        "session_id": session_id,
        "assistant": assistant_text,
        "history": recent + [{"role": "assistant", "content": assistant_text}],
    }
