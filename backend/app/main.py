from datetime import datetime, timezone
import os
import sqlite3
from typing import Any
import uuid

from fastapi import FastAPI
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


app = FastAPI(
    title="Outlook Assistant - General Edition API",
    version="0.1.0",
    description="Foundation API for hybrid SQL + semantic search architecture.",
)


def _db_path() -> str:
    return os.getenv("SQLITE_PATH", "./data/local_search.db")


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
        conn.commit()
    finally:
        conn.close()


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
