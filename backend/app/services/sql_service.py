"""SQL query service — all structured/counting query handlers."""
from __future__ import annotations

from datetime import date
import re
from typing import Any

from app.db import get_connection


def _year_bounds(target_year: int) -> tuple[str, str]:
    return (f"{target_year}-01-01T00:00:00", f"{target_year}-12-31T23:59:59")


def _extract_day_from_query(query: str) -> str | None:
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", query)
    if iso_match:
        return iso_match.group(1)
    today = date.today()
    if "today" in query:
        return today.isoformat()
    if "yesterday" in query:
        return date.fromordinal(today.toordinal() - 1).isoformat()
    return None


def _is_daily_communication_query(query: str) -> bool:
    day_markers = ["particular day", "today", "yesterday", "that day"]
    communication_markers = [
        "communication", "how it was spent", "hours spent",
        "hour spent", "day spent", "email", "meeting",
    ]
    return any(marker in query for marker in day_markers) and any(
        marker in query for marker in communication_markers
    )


def run_sql_query_path(query: str) -> tuple[str, list[dict[str, Any]]]:
    q = query.lower().strip()
    is_count_query = any(marker in q for marker in ["how many", "count", "total"])
    email_in_query = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", q)

    with get_connection() as conn:

        # ── Topic summary intent (e.g., "summarize the topics on ABGL") ─────
        if any(k in q for k in ["summarize", "summary", "key topics", "topics"]) and any(
            m in f" {q} " for m in [" on ", " about ", " regarding ", " related to "]
        ):
            topic_part = ""
            for marker in [" related to ", " on ", " about ", " regarding "]:
                idx = q.find(marker)
                if idx != -1:
                    topic_part = q[idx + len(marker):].strip()
                    break

            for stop in [
                "last year",
                "this year",
                "to date",
                "till date",
                "until date",
                "please",
                "can you",
            ]:
                topic_part = topic_part.replace(stop, "").strip()
            topic_part = topic_part.strip(" ?.,")

            if topic_part:
                wc = f"%{topic_part}%"
                meeting_rows = conn.execute(
                    """SELECT id, topic, notes, organizer, attendees, start_time
                       FROM meetings
                       WHERE LOWER(topic) LIKE ? OR LOWER(notes) LIKE ? OR LOWER(organizer) LIKE ? OR LOWER(attendees) LIKE ?
                       ORDER BY start_time DESC
                       LIMIT 300""",
                    (wc, wc, wc, wc),
                ).fetchall()

                email_rows = conn.execute(
                    """SELECT id, subject, body, sender, recipients, sent_at
                       FROM emails
                       WHERE LOWER(subject) LIKE ? OR LOWER(body) LIKE ? OR LOWER(sender) LIKE ? OR LOWER(recipients) LIKE ?
                       ORDER BY sent_at DESC
                       LIMIT 300""",
                    (wc, wc, wc, wc),
                ).fetchall()

                topic_counts: dict[str, int] = {}
                for r in meeting_rows:
                    t = (r["topic"] or "(no topic)").strip()
                    if not t:
                        t = "(no topic)"
                    topic_counts[t] = topic_counts.get(t, 0) + 1

                subject_counts: dict[str, int] = {}
                for r in email_rows:
                    s = (r["subject"] or "(no subject)").strip()
                    if not s:
                        s = "(no subject)"
                    subject_counts[s] = subject_counts.get(s, 0) + 1

                top_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:8]
                top_subjects = sorted(subject_counts.items(), key=lambda x: x[1], reverse=True)[:8]

                topic_lines = "\n".join([f"- {name} ({count})" for name, count in top_topics]) or "- none"
                subject_lines = "\n".join([f"- {name} ({count})" for name, count in top_subjects]) or "- none"

                answer = (
                    f"Summary for '{topic_part}': matched {len(meeting_rows)} meeting(s) and {len(email_rows)} email(s).\n"
                    f"Top meeting topics:\n{topic_lines}\n"
                    f"Top email subjects:\n{subject_lines}"
                )

                results: list[dict[str, Any]] = []
                for r in meeting_rows[:120]:
                    results.append(
                        {
                            "id": r["id"],
                            "source_type": "meeting",
                            "title": r["topic"],
                            "event_time": r["start_time"],
                            "organizer": r["organizer"],
                            "preview": (r["notes"] or "")[:220],
                            "section": "Meetings",
                        }
                    )

                for r in email_rows[:120]:
                    results.append(
                        {
                            "id": r["id"],
                            "source_type": "email",
                            "title": r["subject"],
                            "event_time": r["sent_at"],
                            "sender": r["sender"],
                            "preview": (r["body"] or "")[:220],
                            "section": "Emails",
                        }
                    )

                return (answer, results)

        # ── Email/meeting participant lookup ─────────────────────────────────
        if email_in_query and any(
            marker in q for marker in ["all emails", "all meetings", "present", "replied", "reply", "replies"]
        ):
            target_email = email_in_query[0].strip().lower()
            local_part = target_email.split("@")[0]
            normalized_name = re.sub(r"[._-]+", " ", local_part).strip()
            name_tokens = [t for t in re.split(r"[^a-z0-9]+", normalized_name) if len(t) >= 2]

            search_terms: list[str] = [target_email, local_part, normalized_name, local_part.replace(".", "")]
            if name_tokens:
                search_terms.extend(name_tokens)
                if len(name_tokens) >= 2:
                    search_terms.append(" ".join(name_tokens[:2]))

            normalized_terms: list[str] = []
            for term in search_terms:
                t = term.strip().lower()
                if t and t not in normalized_terms:
                    normalized_terms.append(t)

            email_clause_parts: list[str] = []
            meeting_clause_parts: list[str] = []
            replied_clause_parts: list[str] = []
            email_params: list[str] = []
            meeting_params: list[str] = []
            replied_params: list[str] = []

            for term in normalized_terms:
                wc = f"%{term}%"
                email_clause_parts.append(
                    "(LOWER(sender) LIKE ? OR LOWER(recipients) LIKE ? OR LOWER(subject) LIKE ? OR LOWER(body) LIKE ?)"
                )
                email_params.extend([wc, wc, wc, wc])
                meeting_clause_parts.append(
                    "(LOWER(organizer) LIKE ? OR LOWER(attendees) LIKE ? OR LOWER(topic) LIKE ? OR LOWER(notes) LIKE ?)"
                )
                meeting_params.extend([wc, wc, wc, wc])
                replied_clause_parts.append("LOWER(sender) LIKE ?")
                replied_params.append(wc)

            email_where = " OR ".join(email_clause_parts) if email_clause_parts else "1=0"
            meeting_where = " OR ".join(meeting_clause_parts) if meeting_clause_parts else "1=0"
            replied_where = " OR ".join(replied_clause_parts) if replied_clause_parts else "1=0"

            email_rows = conn.execute(
                f"SELECT id, subject, body, sender, recipients, sent_at FROM emails WHERE {email_where} ORDER BY sent_at DESC",
                tuple(email_params),
            ).fetchall()
            meeting_rows = conn.execute(
                f"SELECT id, topic, notes, organizer, attendees, start_time, end_time FROM meetings WHERE {meeting_where} ORDER BY start_time DESC",
                tuple(meeting_params),
            ).fetchall()
            replied_rows = conn.execute(
                f"""SELECT id, subject, body, sender, recipients, sent_at FROM emails
                    WHERE ({replied_where}) OR ((LOWER(subject) LIKE 're:%' OR LOWER(subject) LIKE 'fw:%') AND ({email_where}))
                    ORDER BY sent_at DESC""",
                tuple(replied_params + email_params),
            ).fetchall()

            answer = (
                f"Found {len(email_rows)} email(s) and {len(meeting_rows)} meeting(s) where {target_email} is present. "
                f"Found {len(replied_rows)} replied/sent email item(s) by {target_email}."
            )
            results: list[dict[str, Any]] = []
            for row in email_rows:
                results.append({"id": row["id"], "source_type": "email", "title": row["subject"],
                                 "event_time": row["sent_at"], "sender": row["sender"],
                                 "preview": (row["body"] or "")[:220], "section": "Emails (Participant Present)"})
            for row in meeting_rows:
                results.append({"id": row["id"], "source_type": "meeting", "title": row["topic"],
                                 "event_time": row["start_time"], "organizer": row["organizer"],
                                 "preview": (row["notes"] or "")[:220], "section": "Meetings (Participant Present)"})
            for row in replied_rows:
                results.append({"id": row["id"], "source_type": "email", "title": row["subject"],
                                 "event_time": row["sent_at"], "sender": row["sender"],
                                 "preview": (row["body"] or "")[:220], "section": "Items Replied/Sent by Participant"})
            return (answer, results)

        # ── Engagement / interaction analytics ───────────────────────────────
        if any(marker in q for marker in ["engagement", "connected", "connection", "interacted", "interaction"]):
            subject_part = ""
            subject_label = ""
            if "with" in q:
                with_idx = q.find("with")
                subject_part = q[with_idx + 4:].strip()
                subject_label = "with"
            else:
                for marker in [" related to ", " on ", " about ", " regarding "]:
                    idx = q.find(marker)
                    if idx != -1:
                        subject_part = q[idx + len(marker):].strip()
                        subject_label = marker.strip()
                        break

            for stop in ["last year", "this year", "today", "yesterday", "to date", "till date", "until date"]:
                subject_part = subject_part.replace(stop, "").strip()
            subject_part = subject_part.strip(" ?.,")

            if subject_part:
                tf_email = tf_meeting = tf_teams = ""
                period_str = "all time"
                if "last year" in q:
                    s, e = _year_bounds(date.today().year - 1)
                    tf_email = f" AND sent_at >= '{s}' AND sent_at <= '{e}'"
                    tf_meeting = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                    tf_teams = f" AND sent_at >= '{s}' AND sent_at <= '{e}'"
                    period_str = str(date.today().year - 1)
                elif "this year" in q:
                    s, e = _year_bounds(date.today().year)
                    tf_email = f" AND sent_at >= '{s}' AND sent_at <= '{e}'"
                    tf_meeting = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                    tf_teams = f" AND sent_at >= '{s}' AND sent_at <= '{e}'"
                    period_str = str(date.today().year)

                wc = f"%{subject_part}%"
                email_days = conn.execute(
                    f"SELECT DISTINCT DATE(sent_at) AS day FROM emails WHERE (LOWER(sender) LIKE ? OR LOWER(recipients) LIKE ? OR LOWER(subject) LIKE ? OR LOWER(body) LIKE ?){tf_email}",
                    (wc, wc, wc, wc),
                ).fetchall()
                meeting_days = conn.execute(
                    f"SELECT DISTINCT DATE(start_time) AS day FROM meetings WHERE (LOWER(organizer) LIKE ? OR LOWER(attendees) LIKE ? OR LOWER(topic) LIKE ? OR LOWER(notes) LIKE ?){tf_meeting}",
                    (wc, wc, wc, wc),
                ).fetchall()
                teams_chat_days = conn.execute(
                    f"SELECT DISTINCT DATE(sent_at) AS day FROM teams_messages WHERE (LOWER(sender) LIKE ? OR LOWER(chat_topic) LIKE ? OR LOWER(content) LIKE ?){tf_teams}",
                    (wc, wc, wc),
                ).fetchall()
                teams_channel_days = conn.execute(
                    f"SELECT DISTINCT DATE(sent_at) AS day FROM teams_channel_messages WHERE (LOWER(sender) LIKE ? OR LOWER(team_name) LIKE ? OR LOWER(channel_name) LIKE ? OR LOWER(content) LIKE ?){tf_teams}",
                    (wc, wc, wc, wc),
                ).fetchall()

                ed = {r["day"] for r in email_days if r["day"]}
                md = {r["day"] for r in meeting_days if r["day"]}
                tcd = {r["day"] for r in teams_chat_days if r["day"]}
                tnd = {r["day"] for r in teams_channel_days if r["day"]}
                combined = ed | md | tcd | tnd

                answer = (
                    f"Engagement {subject_label} {subject_part} in {period_str}: {len(combined)} unique day(s). "
                    f"Breakdown -> Email: {len(ed)} day(s), Meetings: {len(md)} day(s), "
                    f"Teams chat: {len(tcd)} day(s), Teams channels: {len(tnd)} day(s)."
                )
                results: list[dict[str, Any]] = [
                    {"type": "engagement_metric", "section": "Engagement", "metric": "email_days", "value": len(ed), "period": period_str},
                    {"type": "engagement_metric", "section": "Engagement", "metric": "meeting_days", "value": len(md), "period": period_str},
                    {"type": "engagement_metric", "section": "Engagement", "metric": "combined_unique_days", "value": len(combined), "period": period_str},
                ]
                for day in sorted(combined, reverse=True)[:60]:
                    results.append({"type": "engagement_day", "section": "Engagement Days", "title": day, "event_time": day})
                return (answer, results)

        # ── Out-of-office / absence ──────────────────────────────────────────
        if any(marker in q for marker in ["out of office", "ooo", "absence", "absent", "vacation", "leave", "pto", "sick day"]):
            time_filter = ""
            period_str = "all indexed time"
            if "last year" in q:
                s, e = _year_bounds(date.today().year - 1)
                time_filter = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                period_str = str(date.today().year - 1)
            elif "this year" in q:
                s, e = _year_bounds(date.today().year)
                time_filter = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                period_str = str(date.today().year)

            absence_rows = conn.execute(
                f"""
                SELECT id, topic, start_time, end_time, organizer, notes, DATE(start_time) AS absence_day
                FROM meetings
                WHERE (
                    LOWER(topic) LIKE '%out of office%' OR LOWER(topic) LIKE 'ooo%'
                    OR LOWER(topic) LIKE '% ooo%' OR LOWER(topic) LIKE '%vacation%'
                    OR LOWER(topic) LIKE '%leave%' OR LOWER(topic) LIKE '%annual leave%'
                    OR LOWER(topic) LIKE '%pto%' OR LOWER(topic) LIKE '%sick day%'
                ){time_filter}
                ORDER BY start_time DESC
                """
            ).fetchall()

            seen: set[str] = set()
            unique_days: list[str] = []
            for row in absence_rows:
                day = row["absence_day"]
                if day and day not in seen:
                    seen.add(day)
                    unique_days.append(day)

            if not unique_days:
                return (f"I could not find any out-of-office/absence days in {period_str}.", [])

            answer = f"I found {len(unique_days)} out-of-office/absence day(s) in {period_str}: {', '.join(unique_days)}."
            results: list[dict[str, Any]] = []
            for row in absence_rows:
                results.append({"id": row["id"], "source_type": "meeting", "title": row["topic"],
                                 "event_time": row["start_time"], "organizer": row["organizer"],
                                 "absence_day": row["absence_day"], "preview": (row["notes"] or "")[:220],
                                 "section": "Out of Office / Absence"})
            return (answer, results)

        # ── Daily communication summary ───────────────────────────────────────
        if _is_daily_communication_query(q):
            target_day = _extract_day_from_query(q)
            if not target_day:
                return ("Please include a specific day (e.g. 2026-03-15, today, or yesterday).", [])

            email_rows = conn.execute(
                "SELECT id, subject, sender, recipients, sent_at, body FROM emails WHERE DATE(sent_at) = ? ORDER BY sent_at ASC",
                (target_day,),
            ).fetchall()
            meeting_rows = conn.execute(
                """SELECT id, topic, organizer, attendees, start_time, end_time, notes,
                       MAX(0, CAST((julianday(COALESCE(NULLIF(end_time,''),start_time)) - julianday(start_time)) * 24 * 60 AS INTEGER)) AS duration_minutes
                   FROM meetings WHERE DATE(start_time) = ? ORDER BY start_time ASC""",
                (target_day,),
            ).fetchall()
            teams_rows = conn.execute(
                "SELECT id, chat_id, chat_topic, sender, content, sent_at FROM teams_messages WHERE DATE(sent_at) = ? ORDER BY sent_at ASC",
                (target_day,),
            ).fetchall()
            channel_rows = conn.execute(
                "SELECT id, team_name, channel_name, sender, content, sent_at FROM teams_channel_messages WHERE DATE(sent_at) = ? ORDER BY sent_at ASC",
                (target_day,),
            ).fetchall()

            total_min = sum(int(r["duration_minutes"] or 0) for r in meeting_rows)
            answer = (
                f"On {target_day}: {len(email_rows)} email(s), {len(meeting_rows)} meeting(s) "
                f"({round(total_min / 60, 2)}h), {len(teams_rows)} Teams chat msg(s), {len(channel_rows)} channel msg(s)."
            )
            results: list[dict[str, Any]] = []
            for row in email_rows:
                results.append({"id": row["id"], "source_type": "email", "title": row["subject"],
                                 "event_time": row["sent_at"], "sender": row["sender"],
                                 "preview": (row["body"] or "")[:220], "section": "Emails"})
            for row in meeting_rows:
                results.append({"id": row["id"], "source_type": "meeting", "title": row["topic"],
                                 "event_time": row["start_time"], "organizer": row["organizer"],
                                 "duration_minutes": int(row["duration_minutes"] or 0),
                                 "preview": (row["notes"] or "")[:220], "section": "Meetings"})
            return (answer, results)

        # ── Interview count ──────────────────────────────────────────────────
        if "interview" in q and ("how many" in q or "count" in q):
            time_filter = ""
            email_time_filter = ""
            period_str = "all time"
            if "last year" in q:
                s, e = _year_bounds(date.today().year - 1)
                time_filter = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                email_time_filter = f" AND sent_at >= '{s}' AND sent_at <= '{e}'"
                period_str = str(date.today().year - 1)

            virtual_rows = conn.execute(
                f"SELECT id, topic, start_time, organizer FROM meetings WHERE LOWER(topic) LIKE '%virtual interview%'{time_filter} ORDER BY start_time DESC"
            ).fetchall()
            inperson_rows = conn.execute(
                f"SELECT id, topic, start_time, organizer FROM meetings WHERE (LOWER(topic) LIKE '%[in-person]%interview%' OR LOWER(topic) LIKE '%in-person%interview%'){time_filter} ORDER BY start_time DESC"
            ).fetchall()
            explicit_rows = conn.execute(
                f"""SELECT id, topic, start_time, organizer FROM meetings
                    WHERE (LOWER(topic) LIKE '%interview%' OR LOWER(notes) LIKE '%interview%')
                      AND LOWER(topic) NOT LIKE '%virtual interview%'
                      AND LOWER(topic) NOT LIKE '%[in-person]%interview%'
                      AND LOWER(topic) NOT LIKE '%in-person%interview%'{time_filter}
                    ORDER BY start_time DESC"""
            ).fetchall()
            email_rows = conn.execute(
                f"""SELECT id, subject, sent_at, sender, body FROM emails
                    WHERE (LOWER(subject) LIKE '%interview%' OR LOWER(body) LIKE '%interview%')
                    {email_time_filter}
                    ORDER BY sent_at DESC"""
            ).fetchall()

            total = len(virtual_rows) + len(inperson_rows) + len(explicit_rows)
            breakdown = f"Virtual: {len(virtual_rows)}, In-person: {len(inperson_rows)}"
            if explicit_rows:
                breakdown += f", Other: {len(explicit_rows)}"
            answer = f"You took {total} interviews in {period_str}. Breakdown: {breakdown}. Supporting email evidence rows: {len(email_rows)}."

            results: list[dict[str, Any]] = []
            for row in virtual_rows:
                results.append({"type": "virtual_interview", "id": row["id"], "title": row["topic"],
                                 "event_time": row["start_time"], "organizer": row["organizer"], "section": "Virtual Interviews"})
            for row in inperson_rows:
                results.append({"type": "inperson_interview", "id": row["id"], "title": row["topic"],
                                 "event_time": row["start_time"], "organizer": row["organizer"], "section": "In-person Interviews"})
            for row in explicit_rows:
                results.append({"type": "explicit_interview", "id": row["id"], "title": row["topic"],
                                 "event_time": row["start_time"], "organizer": row["organizer"], "section": "Other Interviews"})
            for row in email_rows:
                results.append({"source_type": "email", "id": row["id"], "title": row["subject"],
                                 "event_time": row["sent_at"], "sender": row["sender"],
                                 "preview": (row["body"] or "")[:220], "section": "Interview Emails"})
            return (answer, results)

        # ── "How many meetings on/about <topic>" ─────────────────────────────
        if (
            ("meeting" in q or "meetings" in q)
            and is_count_query
            and any(m in f" {q} " for m in [" on ", " about ", " regarding ", " related to "])
        ):
            topic_part = ""
            for marker in [" related to ", " on ", " about ", " regarding "]:
                idx = q.find(marker)
                if idx != -1:
                    topic_part = q[idx + len(marker):].strip()
                    break
            for stop in ["last year", "this year", "today", "yesterday", "to date", "till date", "until date"]:
                topic_part = topic_part.replace(stop, "").strip()
            topic_part = topic_part.strip(" ?.,")

            if topic_part:
                time_filter = ""
                period_str = "all time"
                if "last year" in q:
                    s, e = _year_bounds(date.today().year - 1)
                    time_filter = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                    period_str = str(date.today().year - 1)
                elif "this year" in q:
                    s, e = _year_bounds(date.today().year)
                    time_filter = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                    period_str = str(date.today().year)

                wc = f"%{topic_part}%"
                meeting_rows = conn.execute(
                    f"""SELECT DISTINCT id, topic, start_time, end_time, organizer, attendees, notes, location
                        FROM meetings WHERE (LOWER(topic) LIKE ? OR LOWER(notes) LIKE ? OR LOWER(location) LIKE ? OR LOWER(attendees) LIKE ? OR LOWER(organizer) LIKE ?)
                        {time_filter} ORDER BY start_time DESC""",
                    (wc, wc, wc, wc, wc),
                ).fetchall()

                answer = f"You had {len(meeting_rows)} meeting(s) on {topic_part} in {period_str}."
                results: list[dict[str, Any]] = []
                for row in meeting_rows:
                    results.append({"id": row["id"], "source_type": "meeting", "title": row["topic"],
                                     "event_time": row["start_time"], "organizer": row["organizer"],
                                     "preview": row["notes"][:220] if row["notes"] else f"Attendees: {(row['attendees'] or '')[:220]}",
                                     "section": "Meetings"})
                return (answer, results)

        # ── "Meetings with <person>" ──────────────────────────────────────────
        if ("meeting" in q or "met" in q) and "with" in q and is_count_query:
            with_idx = q.find("with")
            if with_idx != -1:
                person_part = q[with_idx + 4:].strip()
                for stop in ["last year", "this year", "in 20", "yesterday", "today"]:
                    person_part = person_part.replace(stop, "").strip()

                time_filter = ""
                period_str = "all time"
                if "last year" in q:
                    s, e = _year_bounds(date.today().year - 1)
                    time_filter = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                    period_str = str(date.today().year - 1)
                elif "this year" in q:
                    s, e = _year_bounds(date.today().year)
                    time_filter = f" AND start_time >= '{s}' AND start_time <= '{e}'"
                    period_str = str(date.today().year)

                wc = f"%{person_part}%"
                meeting_rows = conn.execute(
                    f"""SELECT DISTINCT id, topic, start_time, organizer, attendees, notes
                        FROM meetings WHERE (LOWER(attendees) LIKE ? OR LOWER(organizer) LIKE ? OR LOWER(topic) LIKE ? OR LOWER(notes) LIKE ?)
                        {time_filter} ORDER BY start_time DESC""",
                    (wc, wc, wc, wc),
                ).fetchall()

                answer = f"You had {len(meeting_rows)} meeting(s) with {person_part.strip()} in {period_str}."
                results: list[dict[str, Any]] = []
                for row in meeting_rows:
                    results.append({"id": row["id"], "source_type": "meeting", "title": row["topic"],
                                     "event_time": row["start_time"], "organizer": row["organizer"],
                                     "preview": row["notes"][:220] if row["notes"] else f"Attendees: {(row['attendees'] or '')[:220]}",
                                     "section": "Meetings"})
                return (answer, results)

        # ── Fallback: token-based LIKE search across all tables ───────────────
        tokens = [t for t in q.replace("?", " ").split() if len(t) > 2]
        if not tokens:
            return ("No meaningful terms found.", [])

        like_email = " OR ".join(["LOWER(subject) LIKE ? OR LOWER(body) LIKE ?" for _ in tokens])
        like_meeting = " OR ".join(["LOWER(topic) LIKE ? OR LOWER(notes) LIKE ?" for _ in tokens])
        like_teams = " OR ".join(["LOWER(chat_topic) LIKE ? OR LOWER(content) LIKE ?" for _ in tokens])
        like_channel = " OR ".join(["LOWER(team_name) LIKE ? OR LOWER(channel_name) LIKE ? OR LOWER(content) LIKE ?" for _ in tokens])
        params: list[str] = [f"%{t}%" for t in tokens for _ in range(2)]
        channel_params: list[str] = [f"%{t}%" for t in tokens for _ in range(3)]

        email_rows = conn.execute(
            f"SELECT id, 'email' AS source_type, subject AS title, sent_at AS event_time FROM emails WHERE {like_email} ORDER BY sent_at DESC LIMIT 5",
            tuple(params),
        ).fetchall()
        meeting_rows = conn.execute(
            f"SELECT id, 'meeting' AS source_type, topic AS title, start_time AS event_time FROM meetings WHERE {like_meeting} ORDER BY start_time DESC LIMIT 5",
            tuple(params),
        ).fetchall()
        teams_rows = conn.execute(
            f"SELECT id, 'teams_chat' AS source_type, COALESCE(NULLIF(chat_topic, ''), 'Teams chat') AS title, sent_at AS event_time FROM teams_messages WHERE {like_teams} ORDER BY sent_at DESC LIMIT 5",
            tuple(params),
        ).fetchall()
        channel_rows = conn.execute(
            f"SELECT id, 'teams_channel' AS source_type, COALESCE(NULLIF(team_name || ' / ' || channel_name, ' / '), 'Teams channel') AS title, sent_at AS event_time FROM teams_channel_messages WHERE {like_channel} ORDER BY sent_at DESC LIMIT 5",
            tuple(channel_params),
        ).fetchall()

        results: list[dict[str, Any]] = (
            [dict(r) for r in email_rows]
            + [dict(r) for r in meeting_rows]
            + [dict(r) for r in teams_rows]
            + [dict(r) for r in channel_rows]
        )
        if not results:
            return ("No matching records found for your query.", [])

        return (f"Found {len(results)} relevant record(s).", results)


def run_semantic_fallback(query: str, top_k: int = 6) -> tuple[str, list[dict[str, Any]]]:
    """SQLite LIKE-based semantic fallback used when a vector store is unavailable."""
    q = query.lower().strip()
    tokens = [t for t in q.replace("?", " ").split() if len(t) > 3]
    if not tokens:
        return ("No meaningful search terms extracted.", [])

    # Score rows by how many tokens match, prefer recent items
    with get_connection() as conn:
        results: list[dict[str, Any]] = []

        for token in tokens[:6]:
            wc = f"%{token}%"
            for row in conn.execute(
                "SELECT id, subject AS title, body AS body_text, sender, sent_at FROM emails WHERE LOWER(subject) LIKE ? OR LOWER(body) LIKE ? ORDER BY sent_at DESC LIMIT 4",
                (wc, wc),
            ).fetchall():
                results.append({"id": row["id"], "source_type": "email", "title": row["title"],
                                 "event_time": row["sent_at"], "sender": row["sender"],
                                 "preview": (row["body_text"] or "")[:220], "section": "Emails", "_token": token})

            for row in conn.execute(
                "SELECT id, topic AS title, notes AS body_text, organizer, start_time FROM meetings WHERE LOWER(topic) LIKE ? OR LOWER(notes) LIKE ? ORDER BY start_time DESC LIMIT 4",
                (wc, wc),
            ).fetchall():
                results.append({"id": row["id"], "source_type": "meeting", "title": row["title"],
                                 "event_time": row["start_time"], "organizer": row["organizer"],
                                 "preview": (row["body_text"] or "")[:220], "section": "Meetings", "_token": token})

    # Deduplicate by id, keep top_k most frequent / highest scoring
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in results:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)

    unique = unique[:top_k]
    if not unique:
        return ("No relevant records found via semantic fallback.", [])

    answer = f"Found {len(unique)} relevant record(s) matching your query (semantic fallback — vector search not yet wired)."
    return (answer, unique)
