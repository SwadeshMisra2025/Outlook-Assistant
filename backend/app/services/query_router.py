from dataclasses import dataclass
import re


@dataclass
class QueryIntent:
    mode: str
    reason: str


def classify_query(query: str) -> QueryIntent:
    q = query.lower()

    analytics_markers = ["how many", "count", "last year", "until date", "till date", "to date"]
    day_summary_markers = [
        "particular day",
        "how it was spent",
        "hours spent",
        "day spent",
        "today",
        "yesterday",
    ]
    absence_markers = ["out of office", "ooo", "absence", "absent", "vacation", "leave", "pto", "sick day"]
    teams_markers = ["teams", "chat", "message"]
    engagement_markers = ["engagement", "connected", "connection", "interacted", "interaction"]
    summary_markers = ["summarize", "summary", "key topics", "topics"]

    topic_context = any(m in f" {q} " for m in [" on ", " about ", " regarding ", " related to "])
    topic_subject = any(m in q for m in ["topic", "topics", "theme", "themes", "subject", "subjects"])

    possessive_work = re.search(
        r"\b[a-z]+(?:\s+[a-z]+){0,2}\'s\s+"
        r"(?:work|working|projects?|contributions?|accomplishments?|activities|achievements?|"
        r"involvement|efforts?|deliverables?|responsibilities|initiatives)\b",
        q,
    )
    person_work_question = re.search(
        r"\b(?:what|which|tell|describe|summarize|summary|overview|brief|explain|share|give)\b"
        r".*\b(?:[a-z]+(?:\s+[a-z]+){0,2})\b"
        r".*\b(?:work(?:ing|ed|s)?(?:\s+on)?|accomplish(?:ed)?|contribut(?:e|ed|ing)|"
        r"achiev(?:e|ed|ing)|deliver(?:ed|ing)?|lead|led|build|built|drive|drove|own(?:ed)?|support(?:ed|ing)?)\b",
        q,
    )
    work_overview_request = re.search(
        r"\b(?:summarize|summary|overview|describe|tell me about|brief me on|recap)\b"
        r".*\b(?:work|working|projects?|contributions?|accomplishments?|activities|achievements?|"
        r"deliverables?|initiatives)\b",
        q,
    )

    if (possessive_work or person_work_question or work_overview_request) and not (topic_subject and topic_context):
        return QueryIntent(mode="reasoning", reason="Detected person/work summary intent")

    if any(marker in q for marker in analytics_markers):
        return QueryIntent(mode="sql", reason="Detected counting/date filter intent")
    if any(marker in q for marker in day_summary_markers):
        return QueryIntent(mode="sql", reason="Detected day-level communication summary intent")
    if any(marker in q for marker in absence_markers):
        return QueryIntent(mode="sql", reason="Detected out-of-office/absence intent")
    if any(marker in q for marker in engagement_markers):
        return QueryIntent(mode="sql", reason="Detected engagement analytics intent")
    if any(marker in q for marker in summary_markers) and topic_context:
        return QueryIntent(mode="sql", reason="Detected topic summary intent")
    if any(marker in q for marker in teams_markers):
        return QueryIntent(mode="sql", reason="Detected Teams/chat retrieval intent")

    return QueryIntent(mode="semantic", reason="Defaulting to semantic retrieval")
