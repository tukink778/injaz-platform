"""هجرة قاعدة قديمة (قبل حقول الطالب) إلى البنية الجديدة دون فقد بيانات، وتكرار التشغيل آمن."""
import os, sqlite3, sys, tempfile, pathlib
d = tempfile.mkdtemp(prefix="injaz-mig-"); os.environ["INJAZ_DATA_DIR"] = d
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import importlib; D = importlib.import_module("app.db")

def run():
    if D.IS_PG:   # PostgreSQL: schema معزول (INJAZ_DB_SCHEMA) يُنشأ هنا ثم تُبنى البنية القديمة عبر نفس طبقة التحويل
        c = D.connect()      # الـ schema يُنشأ تلقائياً عند فتح المجمّع
        for st in D._split_statements(D.pg_ddl(D.SCHEMA)): c.execute(st)
    else:
        c = sqlite3.connect(pathlib.Path(d) / "old.db", isolation_level=None, factory=D.SQLiteConn); c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON"); c.executescript(D.SCHEMA)  # البنية القديمة: بلا الأعمدة الجديدة ولا schema_migrations
    assert "gender" not in c.columns("participants")
    c.execute("INSERT INTO departments(code,name_ar) VALUES('X','قسم')"); c.execute("INSERT INTO templates(name,config,created_at) VALUES('t','{}',0)")
    c.execute("INSERT INTO events(dept_id,title_ar,template_id,event_date,reg_token,created_at) VALUES(1,'ف',1,'2026-01-01','t1',0)")
    c.execute("INSERT INTO participants(event_id,name_enc,created_at,dedupe,specialization) VALUES(1,'enc-old',0,'k1','نظم')")
    D.run_migrations(c)
    cols = c.columns("participants")
    assert {"academic_status", "gender", "nationality"} <= cols
    r = c.execute("SELECT * FROM participants").fetchone()
    assert r["name_enc"] == "enc-old" and r["specialization"] == "نظم" and (r["academic_status"], r["gender"], r["nationality"]) == ("", "", ""), "السجل القديم تغيّر"
    D.run_migrations(c); D.run_migrations(c)  # idempotent
    assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3
    t = set(c.tables())
    assert {"user_email_connections", "oauth_states"} <= t
    cols = c.columns("user_email_connections")
    assert {"user_id", "provider", "provider_user_id", "email_address", "encrypted_refresh_token", "token_expiry", "granted_scopes", "connection_status", "created_at", "updated_at", "last_used_at"} <= cols
    c.execute("INSERT INTO users(username,full_name,pw_hash,role,created_at) VALUES('u1','a','x','admin',0)"); c.execute("INSERT INTO users(username,full_name,pw_hash,role,created_at) VALUES('u2','b','x','admin',0)")
    ins = "INSERT INTO user_email_connections(user_id,provider,provider_user_id,email_address,encrypted_refresh_token,created_at,updated_at) VALUES(?,?,?,?,?,0,0)"
    c.execute(ins, (1, "google", "sub1", "a@x.com", "v1.x"))
    for bad, why in (((1, "google", "sub9", "b@x.com", "v1.x"), "مستخدم واحد لا يربط حسابين من المزوّد نفسه"), ((2, "google", "sub1", "a@x.com", "v1.x"), "صندوق بريد واحد لا يُربط بمستخدمين")):
        try: c.execute(ins, bad); raise SystemExit("قيد مفقود: " + why)
        except Exception as e:
            assert "nique" in str(e) or "UNIQUE" in str(e) or "duplicate" in str(e), e
    c.execute(ins, (2, "microsoft", "sub1", "a@x.com", "v1.x"))   # مزوّد مختلف بنفس المعرّف مسموح
    print("MIGRATIONS OK")

if __name__ == "__main__":
    run()
