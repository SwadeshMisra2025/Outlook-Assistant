from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_connection


OL_APPOINTMENT_ITEM = 26
OL_MAIL_ITEM = 43


def _safe_get(item: Any, attr: str, default: str = "") -> str:
    try:
        value = getattr(item, attr)
    except Exception:
        return default

    if value is None:
        return default

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


def _iter_folders(root_folder: Any):
    stack = [root_folder]
    while stack:
        folder = stack.pop()
        yield folder

        try:
            subfolders = folder.Folders
            for index in range(1, int(subfolders.Count) + 1):
                stack.append(subfolders.Item(index))
        except Exception:
            continue


def _collect_attachment_metadata(item: Any) -> list[tuple[str, int | None]]:
    attachments: list[tuple[str, int | None]] = []
    try:
        attachment_items = item.Attachments
        for index in range(1, int(attachment_items.Count) + 1):
            attachment = attachment_items.Item(index)
            name = _safe_get(attachment, "FileName") or _safe_get(attachment, "DisplayName")
            if not name:
                continue
            size_raw = _safe_get(attachment, "Size")
            try:
                size = int(size_raw) if size_raw else None
            except Exception:
                size = None
            attachments.append((name, size))
    except Exception:
        return []
    return attachments


def _ensure_target_tables() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                subject TEXT,
                body TEXT,
                sender TEXT,
                recipients TEXT,
                sent_at TEXT,
                folder TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                topic TEXT,
                notes TEXT,
                organizer TEXT,
                attendees TEXT,
                start_time TEXT,
                end_time TEXT,
                location TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT NOT NULL,
                attachment_name TEXT NOT NULL,
                attachment_size INTEGER,
                UNIQUE(email_id, attachment_name)
            )
            """
        )
        conn.commit()


def ingest_from_outlook_com(max_items: int = 100) -> dict[str, int]:
    try:
        import pythoncom
        import win32com.client  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 is not installed. Run pip install -r backend/requirements.txt"
        ) from exc

    if max_items < 1:
        raise RuntimeError("max_items must be at least 1")

    _ensure_target_tables()

    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
    except Exception as exc:
        pythoncom.CoUninitialize()
        raise RuntimeError(
            "Could not connect to Outlook desktop. Ensure Outlook is installed, open, and signed in."
        ) from exc

    emails_scanned = 0
    emails_inserted = 0
    emails_updated = 0
    meetings_scanned = 0
    meetings_inserted = 0
    meetings_updated = 0

    try:
        with get_connection() as conn:
            stores = namespace.Stores
            for store_index in range(1, int(stores.Count) + 1):
                if emails_scanned >= max_items and meetings_scanned >= max_items:
                    break

                store = stores.Item(store_index)
                root_folder = store.GetRootFolder()

                for folder in _iter_folders(root_folder):
                    if emails_scanned >= max_items and meetings_scanned >= max_items:
                        break

                    try:
                        items = folder.Items
                        item_count = int(items.Count)
                    except Exception:
                        continue

                    for item_index in range(item_count, 0, -1):
                        if emails_scanned >= max_items and meetings_scanned >= max_items:
                            break

                        try:
                            item = items.Item(item_index)
                            item_class = int(getattr(item, "Class", 0))
                        except Exception:
                            continue

                        if item_class == OL_MAIL_ITEM and emails_scanned < max_items:
                            entry_id = _safe_get(item, "EntryID")
                            if not entry_id:
                                continue

                            emails_scanned += 1
                            existing = conn.execute(
                                "SELECT 1 FROM emails WHERE id = ? LIMIT 1",
                                (entry_id,),
                            ).fetchone()

                            sent_time = _safe_get(item, "SentOn") or _safe_get(item, "ReceivedTime")
                            conn.execute(
                                """
                                INSERT INTO emails (id, subject, body, sender, recipients, sent_at, folder)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET
                                    subject=excluded.subject,
                                    body=excluded.body,
                                    sender=excluded.sender,
                                    recipients=excluded.recipients,
                                    sent_at=excluded.sent_at,
                                    folder=excluded.folder
                                """,
                                (
                                    entry_id,
                                    _safe_get(item, "Subject", "(no subject)"),
                                    _safe_get(item, "Body"),
                                    _safe_get(item, "SenderEmailAddress"),
                                    _safe_get(item, "To"),
                                    sent_time,
                                    _safe_get(folder, "FolderPath"),
                                ),
                            )

                            attachment_rows = _collect_attachment_metadata(item)
                            conn.execute(
                                "DELETE FROM email_attachments WHERE email_id = ?",
                                (entry_id,),
                            )
                            for attachment_name, attachment_size in attachment_rows:
                                conn.execute(
                                    """
                                    INSERT OR IGNORE INTO email_attachments
                                    (email_id, attachment_name, attachment_size)
                                    VALUES (?, ?, ?)
                                    """,
                                    (entry_id, attachment_name, attachment_size),
                                )

                            if existing is None:
                                emails_inserted += 1
                            else:
                                emails_updated += 1

                        if item_class == OL_APPOINTMENT_ITEM and meetings_scanned < max_items:
                            entry_id = _safe_get(item, "EntryID")
                            if not entry_id:
                                continue

                            meetings_scanned += 1
                            existing = conn.execute(
                                "SELECT 1 FROM meetings WHERE id = ? LIMIT 1",
                                (entry_id,),
                            ).fetchone()

                            conn.execute(
                                """
                                INSERT INTO meetings (id, topic, notes, organizer, attendees, start_time, end_time, location)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET
                                    topic=excluded.topic,
                                    notes=excluded.notes,
                                    organizer=excluded.organizer,
                                    attendees=excluded.attendees,
                                    start_time=excluded.start_time,
                                    end_time=excluded.end_time,
                                    location=excluded.location
                                """,
                                (
                                    entry_id,
                                    _safe_get(item, "Subject", "(no topic)"),
                                    _safe_get(item, "Body"),
                                    _safe_get(item, "Organizer"),
                                    _safe_get(item, "RequiredAttendees") or _safe_get(item, "OptionalAttendees"),
                                    _safe_get(item, "Start"),
                                    _safe_get(item, "End"),
                                    _safe_get(item, "Location"),
                                ),
                            )

                            if existing is None:
                                meetings_inserted += 1
                            else:
                                meetings_updated += 1
    finally:
        pythoncom.CoUninitialize()

    return {
        "emails_scanned": emails_scanned,
        "emails_inserted": emails_inserted,
        "emails_updated": emails_updated,
        "emails_upserted": emails_inserted + emails_updated,
        "meetings_scanned": meetings_scanned,
        "meetings_inserted": meetings_inserted,
        "meetings_updated": meetings_updated,
        "meetings_upserted": meetings_inserted + meetings_updated,
    }
