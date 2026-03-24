from datetime import datetime, timezone
import os
import re
import sqlite3
from typing import Any
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from ollama import Client
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.db import get_db_path
from app.services.query_router import classify_query
from app.services.reasoning_service import run_reasoning_query_path
from app.services.sql_service import run_sql_query_path, run_semantic_fallback


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    top_k: int = Field(default=25, ge=1, le=500)


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
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db_path() -> str:
    return os.getenv("SQLITE_PATH", "./data/local_search.db")


def _source_db_path() -> str | None:
    explicit = os.getenv("SOURCE_SQLITE_PATH")
    target = os.path.abspath(_db_path())
    if explicit and os.path.exists(explicit):
        explicit_abs = os.path.abspath(explicit)
        if explicit_abs != target:
            return explicit

    cwd = Path.cwd()
    candidates = [
        cwd / ".." / ".." / "Dev1" / "backend" / "data" / "local_search.db",
        cwd / ".." / ".." / "Dev1" / "backend" / "local_search.db",
        cwd / ".." / ".." / ".." / "Dev1" / "backend" / "data" / "local_search.db",
        cwd / ".." / ".." / ".." / "Dev1" / "backend" / "local_search.db",
    ]
    for c in candidates:
        if c.exists():
            resolved = str(c.resolve())
            if os.path.abspath(resolved) != target:
                return resolved
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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _search_runtime_status() -> dict[str, Any]:
    search_db_path = get_db_path()
    source_db = _source_db_path()
    payload: dict[str, Any] = {
        "db_path": search_db_path,
        "db_exists": os.path.exists(search_db_path),
        "source_db_detected": source_db,
        "missing_tables": [],
        "ready": False,
        "message": "",
    }

    if not payload["db_exists"]:
        if source_db:
            payload["message"] = "Search data has not been loaded into the local DB yet. Use Admin Load, or set SOURCE_SQLITE_PATH and retry."
        else:
            payload["message"] = "Search database not found. Set SOURCE_SQLITE_PATH or run Admin Load first."
        return payload

    conn = sqlite3.connect(search_db_path)
    conn.row_factory = sqlite3.Row
    try:
        required_tables = ["emails", "meetings"]
        missing_tables = [table for table in required_tables if not _table_exists(conn, table)]
        payload["missing_tables"] = missing_tables
        if missing_tables:
            payload["message"] = f"Search database is missing required tables: {', '.join(missing_tables)}."
            return payload

        email_count = conn.execute("SELECT COUNT(*) AS count FROM emails").fetchone()["count"]
        meeting_count = conn.execute("SELECT COUNT(*) AS count FROM meetings").fetchone()["count"]
        payload["counts"] = {
            "emails": email_count,
            "meetings": meeting_count,
        }
        payload["ready"] = True
        payload["message"] = "Search runtime is ready."
        if email_count == 0 and meeting_count == 0:
            payload["message"] = "Search database is present but empty. Run Admin Load to copy data into the local DB."
        return payload
    except sqlite3.Error as exc:
        payload["message"] = f"Search database could not be inspected: {exc}"
        return payload
    finally:
        conn.close()


def _search_unavailable_response(query_id: str, req: SearchRequest, route_reason: str, runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": runtime.get("message") or "Search is not ready yet.",
        "mode": "unavailable",
        "results": [],
        "metadata": {
            "query_id": query_id,
            "query": req.query,
            "top_k": req.top_k,
            "route_reason": route_reason,
            "execution_path": ["router", "unavailable"],
            "runtime": runtime,
        },
    }


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
    try:
        dst_conn.execute(row[0])
    except sqlite3.OperationalError as exc:
        # Incremental loads are expected to hit pre-existing tables.
        if "already exists" not in str(exc).lower():
            raise


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

    # Ensure target DB file exists even if source is missing.
    sqlite3.connect(target_path).close()

    if not source_path:
        return {
            "status": "error",
            "message": "No source SQLite found. Full Load copies from a separate source local_search.db. Set SOURCE_SQLITE_PATH in backend/.env (for example: C:/path/to/local_search.db) or place a source DB at a supported auto-detect path.",
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


def _chat_history(session_id: str, limit: int = 40) -> list[dict[str, str]]:
    return _recent_chat(session_id, limit=limit)


def _chat_client() -> Client:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    timeout_seconds = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "8"))
    return Client(host=base_url, timeout=timeout_seconds)


def _rewrite_chat_query(message: str, history: list[dict[str, str]]) -> str:
    trimmed = message.strip()
    if not trimmed:
        return trimmed

    follow_up_pattern = re.compile(
        r"^(and|also|what about|how about|why|when|where|who|which|continue|more|elaborate|expand|compare|show me|tell me more)\b",
        re.IGNORECASE,
    )
    pronoun_pattern = re.compile(r"\b(it|that|those|them|they|he|she|his|her|their|these|this)\b", re.IGNORECASE)
    previous_user_messages = [m["content"] for m in history if m.get("role") == "user"]
    previous_user = previous_user_messages[-1] if previous_user_messages else ""

    # If the new turn already looks self-contained, use it directly.
    if len(trimmed.split()) >= 6 and not follow_up_pattern.search(trimmed) and not pronoun_pattern.search(trimmed):
        return trimmed

    if not previous_user:
        return trimmed

    transcript = "\n".join([f"{m['role']}: {m['content']}" for m in history[-8:]])
    prompt = (
        "Rewrite the user's latest follow-up into one standalone Outlook work-history search question. "
        "Keep concrete names, projects, and dates. Return only the rewritten question.\n\n"
        f"Conversation:\n{transcript}\n"
        f"latest_user_message: {trimmed}"
    )
    try:
        response = _chat_client().chat(
            model=os.getenv("CHAT_MODEL", "mistral").strip() or "mistral",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        rewritten = response.get("message", {}).get("content", "").strip()
        if rewritten:
            return rewritten
    except Exception:
        pass

    base_query = previous_user.strip()
    follow_up = trimmed.strip()

    # Heuristic fallback for time-based follow-ups when rewrite LLM is unavailable.
    if any(marker in follow_up.lower() for marker in ["last year", "this year", "today", "till today", "until today", "to date", "till date", "until date"]):
        for marker in ["last year", "this year", "today", "till today", "until today", "to date", "till date", "until date"]:
            base_query = re.sub(rf"\b{re.escape(marker)}\b", "", base_query, flags=re.IGNORECASE)
        base_query = re.sub(r"\s+", " ", base_query).strip(" ?.,")
        return f"{base_query} {follow_up}".strip()

    return f"{base_query} {follow_up}".strip()


def _run_chat_assistant(message: str, history: list[dict[str, str]]) -> tuple[str, str, str, int]:
    effective_query = _rewrite_chat_query(message, history)
    intent = classify_query(effective_query)

    if intent.mode == "sql":
        answer, results = run_sql_query_path(effective_query)
    elif intent.mode == "reasoning":
        answer, results = run_reasoning_query_path(effective_query, top_k=12)
    else:
        answer, results = run_semantic_fallback(effective_query, top_k=12)

    result_count = len(results)
    if effective_query != message.strip():
        assistant_text = (
            f"Interpreted follow-up as: {effective_query}\n\n"
            f"{answer}\n\n"
            f"Route: {intent.mode}. Evidence rows: {result_count}."
        )
    else:
        assistant_text = f"{answer}\n\nRoute: {intent.mode}. Evidence rows: {result_count}."

    return assistant_text, effective_query, intent.mode, result_count


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


def _normalize_sender(raw_sender: str | None) -> str:
    if not raw_sender:
        return "unknown"

    s = raw_sender.strip()
    if not s:
        return "unknown"

    # Prefer a real email when present in display-name formats.
    email_match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", s)
    if email_match:
        return email_match.group(0).lower()

    lowered = s.lower()
    # Outlook/Exchange internal legacy DN values are not human-friendly for charts.
    if lowered.startswith("/o=") or lowered.startswith("/ou=") or "/cn=recipients/" in lowered:
        return "exchange_internal_sender"

    return lowered


def _normalize_person_token(raw_value: str) -> str:
    v = (raw_value or "").strip().strip('"').strip("'")
    v = re.sub(r"\s+", " ", v)
    return v


def _is_self_person(token: str) -> bool:
    t = token.lower()
    aliases = [
        "swadesh",
        "swadesh misra",
        "swadesh.misra@gmail.com",
        "me@company.com",
    ]
    return any(a in t for a in aliases)


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
            "emails_by_correspondent": [],
            "email_participant_mix": {"one_to_one": 0, "group": 0},
            "meeting_participant_mix": {"one_to_one": 0, "group": 0},
            "meta": {"sqlite_path": path, "warnings": [], "total_emails": 0, "total_meetings": 0},
        }

        if _table_exists(conn, "emails"):
            total_emails_row = conn.execute("SELECT COUNT(*) AS c FROM emails").fetchone()
            result["meta"]["total_emails"] = int(total_emails_row["c"] if total_emails_row else 0)
            sender_col = _first_existing_column(conn, "emails", ["sender", "sender_email", "from_email", "from_address"])
            recipient_col = _first_existing_column(conn, "emails", ["recipients", "to_recipients", "to_emails", "participant_emails"])

            if sender_col:
                rows = conn.execute(
                    f"SELECT COALESCE({sender_col}, '') AS sender, COUNT(*) AS c FROM emails GROUP BY sender ORDER BY c DESC",
                ).fetchall()
                sender_counts: dict[str, int] = {}
                for r in rows:
                    key = _normalize_sender(r["sender"])
                    sender_counts[key] = sender_counts.get(key, 0) + int(r["c"])

                sorted_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)
                result["emails_by_sender"] = [
                    {"sender": sender, "count": count}
                    for sender, count in sorted_senders[:top_n]
                ]
            else:
                result["meta"]["warnings"].append("No sender-like column found in emails table.")

            if recipient_col:
                rows = conn.execute(f"SELECT {recipient_col} AS recipients FROM emails").fetchall()
                one = 0
                group = 0
                correspondents: dict[str, int] = {}
                for r in rows:
                    recipients = _split_people(r["recipients"])
                    if len(recipients) <= 1:
                        one += 1
                    else:
                        group += 1

                    for p in recipients:
                        person = _normalize_person_token(p)
                        if not person:
                            continue
                        if person.lower() in {"unknown", "undisclosed recipients"}:
                            continue
                        if _is_self_person(person):
                            continue
                        key = person.lower()
                        correspondents[key] = correspondents.get(key, 0) + 1

                result["email_participant_mix"] = {"one_to_one": one, "group": group}
                result["emails_by_correspondent"] = [
                    {"person": person, "count": count}
                    for person, count in sorted(correspondents.items(), key=lambda x: x[1], reverse=True)[:top_n]
                ]
            else:
                result["meta"]["warnings"].append("No recipient-like column found in emails table.")
        else:
            result["meta"]["warnings"].append("Emails table not found.")

        if _table_exists(conn, "meetings"):
            total_meetings_row = conn.execute("SELECT COUNT(*) AS c FROM meetings").fetchone()
            result["meta"]["total_meetings"] = int(total_meetings_row["c"] if total_meetings_row else 0)
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
        "version": "0.2.0",
        "flow": [
            "Admin load stage (incremental/full copy from source SQLite into local DB)",
            "Search/chat request intake with session-aware follow-up rewrite",
            "Intent routing (sql, reasoning, semantic)",
            "Execution lanes: SQL analytics, reasoning summarization, semantic fallback",
            "Grounded response synthesis with execution metadata",
            "Feedback and observability loop (completeness + metrics + history)",
        ],
        "components": {
            "api": {
                "health": "/api/health",
                "search": "/api/search",
                "chat": "/api/chat/message",
                "chat_session": "/api/chat/session/{session_id}",
                "metrics": "/api/metrics",
                "completeness": "/api/metrics/completeness",
                "feedback": "/api/feedback/completeness",
                "admin_load": "/api/admin/load",
                "admin_load_status": "/api/admin/load-status",
                "technology_map": "/api/technology-map",
            },
            "data": {
                "sql_path": _db_path(),
                "tracking_path": _tracking_db_path(),
            },
            "ui": {
                "tabs": ["Workbench", "Architecture", "Technology and Flow", "Admin"],
                "floating_panels": [
                    "Chat Assistant (minimizable, session-aware)",
                    "Metrics Results (minimizable, docked to bottom-left on desktop)",
                ],
            },
        },
        "modes": ["sql", "reasoning", "semantic"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/technology-map")
def technology_map() -> dict[str, Any]:
    return {
        "title": "Outlook Assistant Technology Flow",
        "objective": "Help local builders understand architecture, stack choices, and high-impact improvement areas.",
        "stages": [
            {
                "name": "1) Data Sync and Ingestion",
                "purpose": "Bring source data into this repo's local SQLite target for deterministic local querying.",
                "technologies": ["SQLite", "FastAPI admin endpoints", "Python ETL copy utilities"],
                "current_state": "Implemented incremental/full admin load path with run tracking.",
                "improvements": [
                    "Add per-table watermark persistence for faster incremental loads.",
                    "Capture row-level reconciliation metrics for each load run.",
                    "Add optional source connectors (Outlook/Graph) as pluggable ingestors.",
                ],
            },
            {
                "name": "2) SQL Facts Lane",
                "purpose": "Answer deterministic count and numeric analytics queries.",
                "technologies": ["SQLite", "SQL aggregations", "FastAPI"],
                "current_state": "Implemented for counts, time windows, engagement views, and participant lookups.",
                "improvements": [
                    "Add schema migration tooling.",
                    "Add reusable query templates for recurring business questions.",
                    "Add metric cache for expensive aggregations.",
                ],
            },
            {
                "name": "3) Reasoning Lane",
                "purpose": "Build person/work summaries using evidence from meetings and emails over requested periods.",
                "technologies": ["LangGraph", "Ollama", "Regex entity/time extraction", "SQLite evidence joins"],
                "current_state": "Implemented and routed through query intent classification.",
                "improvements": [
                    "Add confidence scoring and explicit evidence coverage indicators.",
                    "Add person alias learning from feedback and usage history.",
                    "Add richer time constraints (quarter, month-range, fiscal year).",
                ],
            },
            {
                "name": "4) Semantic Fallback",
                "purpose": "Provide broad exploratory fallback when strict SQL or reasoning routes are not selected.",
                "technologies": ["Fallback retriever", "SQLite text scan", "Answer synthesis"],
                "current_state": "Implemented as default route when SQL/reasoning signals are absent.",
                "improvements": [
                    "Introduce vector retrieval backend for higher recall.",
                    "Add section-specific retrieval weighting (emails vs meetings).",
                    "Return retrieval diagnostics (hit quality, coverage, latency).",
                ],
            },
            {
                "name": "5) Conversation, Feedback, and UX",
                "purpose": "Support iterative Q&A with session context and operator-facing observability panels.",
                "technologies": [
                    "Tracking SQLite",
                    "Session history",
                    "Follow-up rewrite",
                    "Completeness feedback",
                    "Floating/minimizable UI panels",
                ],
                "current_state": "Implemented chat sessions, query rewrite, completeness metrics, and docked metrics/chat panels.",
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
            "Use Admin tab to sync source data into local target DB",
            "Use Workbench + Chat panels to validate routing behavior",
            "Use Architecture and Technology tabs to inspect current stack and roadmap",
            "Validate quality with metrics and completeness feedback loops",
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
    try:
        payload = _run_admin_load(mode=req.mode)
    except Exception as exc:
        payload = {
            "status": "error",
            "mode": req.mode,
            "message": f"Admin load execution failed: {exc}",
            "source_db": _source_db_path(),
            "target_db": _db_path(),
            "tables": [],
        }

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
    query_id = str(uuid.uuid4())
    runtime = _search_runtime_status()
    if not runtime.get("ready"):
        _log_semantic_query(query_id=query_id, query_text=req.query, top_k=req.top_k, mode="unavailable")
        return _search_unavailable_response(
            query_id=query_id,
            req=req,
            route_reason="search runtime unavailable",
            runtime=runtime,
        )

    intent = classify_query(req.query)
    execution_mode = intent.mode
    warnings: list[str] = []
    _log_semantic_query(query_id=query_id, query_text=req.query, top_k=req.top_k, mode=intent.mode)

    try:
        if intent.mode == "sql":
            answer, results = run_sql_query_path(req.query)
        elif intent.mode == "reasoning":
            answer, results = run_reasoning_query_path(req.query, top_k=req.top_k)
        else:
            answer, results = run_semantic_fallback(req.query, top_k=req.top_k)
    except Exception as exc:
        warnings.append(f"Primary {intent.mode} route failed: {exc}")
        try:
            answer, results = run_semantic_fallback(req.query, top_k=req.top_k)
            execution_mode = "semantic-fallback"
            warnings.append("Returned semantic fallback results instead of failing the whole request.")
        except Exception as fallback_exc:
            runtime["message"] = f"Search failed after fallback: {fallback_exc}"
            runtime["errors"] = warnings + [f"Fallback semantic route failed: {fallback_exc}"]
            return _search_unavailable_response(
                query_id=query_id,
                req=req,
                route_reason=intent.reason,
                runtime=runtime,
            )

    return {
        "answer": answer,
        "mode": execution_mode,
        "results": results[:req.top_k],
        "metadata": {
            "query_id": query_id,
            "query": req.query,
            "top_k": req.top_k,
            "route_reason": intent.reason,
            "execution_path": ["router", execution_mode, "synthesis"],
            "warnings": warnings,
            "runtime": runtime,
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
    prior_history = _chat_history(session_id, limit=12)
    _log_chat_message(session_id, "user", req.message)

    assistant_text, effective_query, mode, result_count = _run_chat_assistant(req.message, prior_history)
    _log_chat_message(session_id, "assistant", assistant_text)
    history = _chat_history(session_id, limit=40)

    return {
        "session_id": session_id,
        "assistant": assistant_text,
        "history": history,
        "metadata": {
            "effective_query": effective_query,
            "mode": mode,
            "result_count": result_count,
        },
    }


@app.get("/api/chat/session/{session_id}")
def chat_session(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "history": _chat_history(session_id, limit=40),
    }


# Serve the static frontend.  Must be mounted AFTER all /api/* routes so that
# API paths are resolved first.  Works both when cwd is backend/ and when the
# caller sets --app-dir backend.
_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
