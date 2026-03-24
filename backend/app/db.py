import os
import sqlite3


def get_db_path() -> str:
    explicit = os.getenv("SQLITE_PATH")
    if explicit:
        return explicit

    default_path = "./data/local_search.db"
    if os.path.exists(default_path):
        return default_path

    source_path = os.getenv("SOURCE_SQLITE_PATH")
    if source_path and os.path.exists(source_path):
        return source_path

    return default_path


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn
