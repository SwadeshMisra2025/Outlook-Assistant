import os
import sqlite3


def _db_path() -> str:
    return os.getenv("SQLITE_PATH", "./data/local_search.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn
