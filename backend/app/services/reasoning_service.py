from __future__ import annotations

from collections import Counter
from datetime import date
import os
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from ollama import Client

from app.db import get_connection


SELF_PERSON_NAME = "me"
SELF_ALIASES = [
    "swadesh",
    "swadesh misra",
    "swadesh.misra@gmail.com",
    "swadeshmisra",
]


class ReasoningState(TypedDict, total=False):
    query: str
    top_k: int
    person_name: str
    aliases: list[str]
    period_start: str | None
    period_end: str | None
    period_label: str
    email_rows: list[dict[str, Any]]
    meeting_rows: list[dict[str, Any]]
    topic_counts: list[tuple[str, int]]
    subject_counts: list[tuple[str, int]]
    month_counts: list[tuple[str, int]]
    answer: str
    results: list[dict[str, Any]]


def _year_bounds(target_year: int) -> tuple[str, str]:
    return (f"{target_year}-01-01T00:00:00", f"{target_year}-12-31T23:59:59")


def _clean_title(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    text = re.sub(r"^(re|fw|fwd):\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip() or fallback


def _extract_person_name(query: str) -> str:
    q = query.lower()
    if re.search(r"\b(my|me|i)\b", q):
        return SELF_PERSON_NAME

    patterns = [
        r"summari(?:s|z)e\s+([a-z][a-z .-]+?)'s\s+work",
        r"summary\s+of\s+([a-z][a-z .-]+?)'s\s+work",
        r"summari(?:s|z)e\s+the\s+work\s+of\s+([a-z][a-z .-]+)",
        r"summari(?:s|z)e\s+work\s+done\s+by\s+([a-z][a-z .-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ?.,")
    return "the requested person"


def _extract_period(query: str) -> tuple[str | None, str | None, str]:
    q = query.lower()
    if any(marker in q for marker in ["till today", "until today", "to today", "till date", "until date", "to date"]):
        return None, f"{date.today().isoformat()}T23:59:59", "up to today"
    if "last year" in q:
        year = date.today().year - 1
        start, end = _year_bounds(year)
        return start, end, str(year)
    if "this year" in q:
        year = date.today().year
        start, end = _year_bounds(year)
        return start, end, str(year)

    match = re.search(r"\b(20\d{2})\b", q)
    if match:
        year = int(match.group(1))
        start, end = _year_bounds(year)
        return start, end, str(year)

    return None, None, "all indexed time"


def _build_aliases(person_name: str) -> list[str]:
    normalized = person_name.lower().strip()
    if normalized == SELF_PERSON_NAME:
        return SELF_ALIASES.copy()

    aliases: list[str] = []
    if normalized and normalized != "the requested person":
        aliases.append(normalized)
        tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if len(token) >= 3]
        if tokens:
            aliases.append(tokens[0])
        if len(tokens) >= 2:
            aliases.append(" ".join(tokens[:2]))

    unique: list[str] = []
    for alias in aliases:
        if alias and alias not in unique:
            unique.append(alias)
    return unique


def _build_like_clause(columns: list[str], aliases: list[str]) -> tuple[str, list[str]]:
    clause_parts: list[str] = []
    params: list[str] = []
    for alias in aliases:
        wc = f"%{alias}%"
        clause_parts.append("(" + " OR ".join([f"LOWER({column}) LIKE ?" for column in columns]) + ")")
        params.extend([wc] * len(columns))
    return " OR ".join(clause_parts) if clause_parts else "1=0", params


def _fetch_rows(state: ReasoningState) -> ReasoningState:
    aliases = state.get("aliases") or []
    person_name = state.get("person_name") or "the requested person"
    period_start = state.get("period_start")
    period_end = state.get("period_end")

    if not aliases:
        return {
            "answer": f"I could not determine which person to summarize from the query: {state['query']}",
            "results": [],
            "email_rows": [],
            "meeting_rows": [],
            "topic_counts": [],
            "subject_counts": [],
            "month_counts": [],
        }

    email_clause, email_params = _build_like_clause(["sender", "recipients", "subject", "body"], aliases)
    meeting_clause, meeting_params = _build_like_clause(["organizer", "attendees", "topic", "notes"], aliases)

    email_sql = (
        "SELECT id, subject, body, sender, recipients, sent_at FROM emails "
        f"WHERE {email_clause}"
    )
    meeting_sql = (
        "SELECT id, topic, notes, organizer, attendees, start_time FROM meetings "
        f"WHERE {meeting_clause}"
    )

    if period_start:
        email_sql += " AND sent_at >= ?"
        meeting_sql += " AND start_time >= ?"
        email_params.append(period_start)
        meeting_params.append(period_start)
    if period_end:
        email_sql += " AND sent_at <= ?"
        meeting_sql += " AND start_time <= ?"
        email_params.append(period_end)
        meeting_params.append(period_end)

    email_sql += " ORDER BY sent_at DESC LIMIT 180"
    meeting_sql += " ORDER BY start_time DESC LIMIT 180"

    with get_connection() as conn:
        email_rows = [dict(row) for row in conn.execute(email_sql, tuple(email_params)).fetchall()]
        meeting_rows = [dict(row) for row in conn.execute(meeting_sql, tuple(meeting_params)).fetchall()]

    if not email_rows and not meeting_rows:
        return {
            "answer": f"I could not find emails or meetings for {person_name} in {state['period_label']}.",
            "results": [],
            "email_rows": [],
            "meeting_rows": [],
            "topic_counts": [],
            "subject_counts": [],
            "month_counts": [],
        }

    return {
        "email_rows": email_rows,
        "meeting_rows": meeting_rows,
    }


def _derive_signals(state: ReasoningState) -> ReasoningState:
    email_rows = state.get("email_rows") or []
    meeting_rows = state.get("meeting_rows") or []

    topic_counter: Counter[str] = Counter()
    subject_counter: Counter[str] = Counter()
    month_counter: Counter[str] = Counter()

    for row in meeting_rows:
        topic_counter[_clean_title(row.get("topic"), "(no topic)")] += 1
        start_time = row.get("start_time")
        if start_time:
            month_counter[start_time[:7]] += 1

    for row in email_rows:
        subject_counter[_clean_title(row.get("subject"), "(no subject)")] += 1
        sent_at = row.get("sent_at")
        if sent_at:
            month_counter[sent_at[:7]] += 1

    return {
        "topic_counts": topic_counter.most_common(8),
        "subject_counts": subject_counter.most_common(8),
        "month_counts": month_counter.most_common(6),
    }


def _build_results(state: ReasoningState) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []

    for row in state.get("meeting_rows") or []:
        combined.append(
            {
                "id": row["id"],
                "source_type": "meeting",
                "title": row.get("topic"),
                "event_time": row.get("start_time"),
                "organizer": row.get("organizer"),
                "preview": (row.get("notes") or "")[:220],
                "section": "Meetings",
            }
        )

    for row in state.get("email_rows") or []:
        combined.append(
            {
                "id": row["id"],
                "source_type": "email",
                "title": row.get("subject"),
                "event_time": row.get("sent_at"),
                "sender": row.get("sender"),
                "preview": (row.get("body") or "")[:220],
                "section": "Emails",
            }
        )

    combined.sort(key=lambda item: item.get("event_time") or "", reverse=True)
    return combined


def _deterministic_summary(state: ReasoningState) -> str:
    person_name = state.get("person_name") or "the requested person"
    period_label = state.get("period_label") or "all indexed time"
    meeting_count = len(state.get("meeting_rows") or [])
    email_count = len(state.get("email_rows") or [])

    lines = [
        f"Summary for {person_name} in {period_label}: found {meeting_count} meeting(s) and {email_count} email(s) tied to this person.",
    ]

    month_counts = state.get("month_counts") or []
    if month_counts:
        month_text = ", ".join([f"{month} ({count})" for month, count in month_counts[:4]])
        lines.append(f"Most active periods: {month_text}.")

    topic_counts = state.get("topic_counts") or []
    if topic_counts:
        lines.append("Recurring meeting topics:")
        lines.extend([f"- {topic} ({count})" for topic, count in topic_counts[:5]])

    subject_counts = state.get("subject_counts") or []
    if subject_counts:
        lines.append("Recurring email threads:")
        lines.extend([f"- {subject} ({count})" for subject, count in subject_counts[:5]])

    return "\n".join(lines)


def _llm_summary(state: ReasoningState) -> str | None:
    chat_model = os.getenv("CHAT_MODEL", "mistral").strip() or "mistral"
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    timeout_seconds = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "8"))

    evidence_rows = _build_results(state)[:18]
    evidence_lines: list[str] = []
    for row in evidence_rows:
        title = _clean_title(row.get("title"), "(no title)")
        when = row.get("event_time") or "unknown time"
        preview = re.sub(r"\s+", " ", (row.get("preview") or "")).strip()
        evidence_lines.append(f"- [{row.get('source_type')}] {when} | {title} | {preview[:240]}")

    prompt = (
        f"You are summarizing a colleague's work from Outlook evidence.\n"
        f"Person: {state.get('person_name')}\n"
        f"Period: {state.get('period_label')}\n"
        f"Meeting count: {len(state.get('meeting_rows') or [])}\n"
        f"Email count: {len(state.get('email_rows') or [])}\n"
        f"Top meeting topics: {state.get('topic_counts') or []}\n"
        f"Top email subjects: {state.get('subject_counts') or []}\n"
        "Produce a concise factual summary with these sections exactly: Overview, Main Workstreams, Signals, Caveats. "
        "Only use information present in the evidence. Do not invent missing context.\n"
        "Evidence:\n" + "\n".join(evidence_lines)
    )

    try:
        client = Client(host=base_url, timeout=timeout_seconds)
        response = client.chat(
            model=chat_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
    except Exception:
        return None

    content = response.get("message", {}).get("content", "").strip()
    return content or None


def _synthesize_answer(state: ReasoningState) -> ReasoningState:
    if not (state.get("meeting_rows") or state.get("email_rows")) and state.get("answer"):
        return {
            "answer": state["answer"],
            "results": [],
        }

    results = _build_results(state)
    answer = _llm_summary(state) or _deterministic_summary(state)
    return {
        "answer": answer,
        "results": results[: state.get("top_k", 25)],
    }


def _build_graph():
    graph = StateGraph(ReasoningState)
    graph.add_node("fetch_rows", _fetch_rows)
    graph.add_node("derive_signals", _derive_signals)
    graph.add_node("synthesize_answer", _synthesize_answer)
    graph.set_entry_point("fetch_rows")
    graph.add_edge("fetch_rows", "derive_signals")
    graph.add_edge("derive_signals", "synthesize_answer")
    graph.add_edge("synthesize_answer", END)
    return graph.compile()


_GRAPH = _build_graph()


def run_reasoning_query_path(query: str, top_k: int = 25) -> tuple[str, list[dict[str, Any]]]:
    person_name = _extract_person_name(query)
    period_start, period_end, period_label = _extract_period(query)
    state: ReasoningState = {
        "query": query,
        "top_k": top_k,
        "person_name": person_name,
        "aliases": _build_aliases(person_name),
        "period_start": period_start,
        "period_end": period_end,
        "period_label": period_label,
    }
    result = _GRAPH.invoke(state)
    return result.get("answer", "No reasoning summary was produced."), result.get("results", [])