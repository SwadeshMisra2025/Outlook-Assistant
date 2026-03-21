import sqlite3, os

dbs = [
    r"C:\Swadesh\Aletha\Outlook-research\Aletha-One-General-Edition\backend\data\local_search.db",
    r"C:\Swadesh\Aletha\Outlook-research\Dev1\backend\local_search.db",
]
for path in dbs:
    print("=" * 60)
    print("DB:", path)
    print("SIZE:", os.path.getsize(path), "bytes")
    con = sqlite3.connect(path)
    tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for t in tables:
        count = con.execute("SELECT COUNT(*) FROM " + t[0]).fetchone()[0]
        cols = [c[1] for c in con.execute("PRAGMA table_info(" + t[0] + ")").fetchall()]
        print(f"  TABLE {t[0]}: {count} rows | cols: {cols}")
    con.close()
