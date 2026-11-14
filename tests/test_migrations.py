"""هجرة قاعدة قديمة (قبل حقول الطالب) إلى البنية الجديدة دون فقد بيانات، وتكرار التشغيل آمن."""
import os, sqlite3, sys, tempfile, pathlib
d = tempfile.mkdtemp(prefix="injaz-mig-"); os.environ["INJAZ_DATA_DIR"] = d
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import importlib; D = importlib.import_module("app.db")

def run():
    c = sqlite3.connect(pathlib.Path(d) / "old.db", isolation_level=None); c.row_factory = sqlite3.Row
    c.executescript(D.SCHEMA)  # البنية القديمة: بلا الأعمدة الجديدة ولا schema_migrations
    assert "gender" not in {r[1] for r in c.execute("PRAGMA table_info(participants)")}
    c.execute("INSERT INTO departments(code,name_ar) VALUES('X','قسم')"); c.execute("INSERT INTO templates(name,config,created_at) VALUES('t','{}',0)")
    c.execute("INSERT INTO events(dept_id,title_ar,template_id,event_date,reg_token,created_at) VALUES(1,'ف',1,'2026-01-01','t1',0)")
    c.execute("INSERT INTO participants(event_id,name_enc,created_at,dedupe,specialization) VALUES(1,'enc-old',0,'k1','نظم')")
    D.run_migrations(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(participants)")}
    assert {"academic_status", "gender", "nationality"} <= cols
    r = c.execute("SELECT * FROM participants").fetchone()
    assert r["name_enc"] == "enc-old" and r["specialization"] == "نظم" and (r["academic_status"], r["gender"], r["nationality"]) == ("", "", ""), "السجل القديم تغيّر"
    D.run_migrations(c); D.run_migrations(c)  # idempotent
    assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    print("MIGRATIONS OK")

if __name__ == "__main__":
    run()
