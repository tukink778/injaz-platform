"""محتوى القاعدة الخام للبحث عن نصوص صريحة: ملفات SQLite (مع WAL) أو تفريغ نصي لكل جداول PostgreSQL."""
import pathlib


def raw_db_bytes(data_dir, connect, is_pg):
    if not is_pg:
        p = pathlib.Path(data_dir) / "injaz.db"; w = pathlib.Path(data_dir) / "injaz.db-wal"
        return p.read_bytes() + (w.read_bytes() if w.exists() else b"")
    c = connect(); out = []
    for t in c.tables():
        for r in c.execute(f"SELECT * FROM {t}").fetchall():
            out.append(repr(list(r)))
    c.close()
    return "\n".join(out).encode("utf-8", "backslashreplace") + "\n".join(out).encode("utf-8")
