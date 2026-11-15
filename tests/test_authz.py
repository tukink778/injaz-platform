"""مصفوفة صلاحيات منهجية: كل مسار × (مجهول، منظّم من قسم آخر، مطّلع) يجب أن يُرفض كما هو متوقع."""
import re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_e2e as T
from test_e2e import app, tok, login, post, connect

def run():
    T.test_all()  # يجهّز بيانات حقيقية: قسمان، منظّم في القسم 2، مطّلع في القسم 1، فعالية وشهادات في القسم 1
    db = connect(); cl = app.test_client(); assert login(cl, "admin", "Admin-Pass-12345").status_code == 302
    pw = re.search(r"mono\">([^<]+)<", cl.post("/admin/users", data=dict(_csrf=tok(cl, "/admin/users"), act="reset", uid="3")).get_data(True)).group(1)
    v = app.test_client(); assert login(v, "view1", pw).status_code == 302
    assert v.post("/account", data=dict(_csrf=tok(v, "/account"), act="pw", old=pw, new="Viewer-Pass-77889", new2="Viewer-Pass-77889")).status_code == 302
    o = app.test_client(); assert login(o, "org1", "Organizer-Pass-9988").status_code == 302
    anon = app.test_client()
    eid = db.execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()[0]
    pid = db.execute("SELECT id FROM participants WHERE event_id=? LIMIT 1", (eid,)).fetchone()[0]
    cid = db.execute("SELECT id FROM certificates WHERE event_id=? LIMIT 1", (eid,)).fetchone()[0]
    jid = db.execute("SELECT id FROM jobs ORDER BY id LIMIT 1").fetchone()[0]
    uidx = db.execute("SELECT uid_idx FROM participants WHERE uid_idx!='' LIMIT 1").fetchone()[0]
    vals = dict(eid=eid, pid=pid, cid=cid, jid=jid, tid=1, uid_idx=uidx, token="x" * 20, code="AAAA-BBBB-CCCC", name="x" * 32, serial="S")
    PUBLIC = ("public.", "auth.login", "static", "healthz")  # healthz: فحص جاهزية بلا بيانات ولا أسرار
    checked = dict(anon=0, org=0, view=0)
    for rule in app.url_map.iter_rules():
        ep = rule.endpoint
        if ep == "static": continue
        path = re.sub(r"<(?:\w+:)?(\w+)>", lambda m: str(vals.get(m.group(1), 1)), rule.rule)
        for method in sorted(rule.methods & {"GET", "POST"}):
            def call(cl_, tkn_page="/"):
                if method == "GET": return cl_.get(path)
                return cl_.post(path, data=dict(_csrf=tok(cl_, tkn_page) or "x"))
            # 1) مجهول
            if not ep.startswith(PUBLIC):
                r = call(anon, "/login"); anon_ok = r.status_code in (302, 400, 401, 403, 404, 405) and (r.status_code != 302 or "/login" in r.headers.get("Location", ""))
                assert anon_ok, f"مجهول وصل إلى {method} {path}: {r.status_code}"; checked["anon"] += 1
            # 2) منظّم من قسم آخر: أي مسار مرتبط بفعالية/مشارك/شهادة/مهمة أو إداري يجب أن يُرفض
            EXEMPT = ("admin.template_preview",)  # معاينة ببيانات عامة، متاحة للمنظّمين عمداً عند اختيار القالب
            obj = ep not in EXEMPT and (any(k in rule.rule for k in ("<int:eid>", "<int:pid>", "<int:cid>", "<int:jid>", "<uid_idx>")) or ep.startswith("admin."))
            if obj and not ep.startswith(PUBLIC):
                r = call(o); assert r.status_code in (403, 404), f"منظّم القسم الآخر وصل إلى {method} {path}: {r.status_code}"; checked["org"] += 1
            # 3) مطّلع: لا يكتب ولا يدير
            write = method == "POST" and not ep.startswith(PUBLIC) and ep not in ("auth.logout", "auth.account", "ui.api_search", "ai.assistant", "ai.data", "ai.designer", "ai.designer_create")
            if (write or ep.startswith("admin.")) and ep not in EXEMPT:
                r = call(v); assert r.status_code == 403, f"مطّلع نفّذ {method} {path}: {r.status_code}"; checked["view"] += 1
    assert min(checked.values()) > 20, checked
    # انحدار: صفحة تقدّم المهمة تعمل للمخوَّل (كانت 500) ولا تسرّب عنوان الفعالية لقسم آخر عبر مسار التنقل
    TITLE = "ورشة الذكاء الاصطناعي"
    rj = cl.get(f"/jobs/{jid}"); assert rj.status_code == 200 and TITLE in rj.get_data(True), rj.status_code
    for url in (f"/jobs/{jid}", f"/events/{eid}", f"/events/{eid}/flow", f"/events/{eid}/edit"):
        ro = o.get(url); assert ro.status_code in (403, 404) and TITLE not in ro.get_data(True), (url, ro.status_code)
    # روابط الشهادة العامة: رمز التحقق لا يكشف البريد/الرقم الجامعي، والتحميل برابط مجهول التخمين
    code = db.execute("SELECT verify_code FROM certificates WHERE status='valid' LIMIT 1").fetchone()[0]
    html = anon.get(f"/v/{code}").get_data(True); assert "@" not in html and "22S" not in html
    assert anon.get("/get/" + "A" * 43).status_code in (404, 410)
    print("AUTHZ OK", checked)

if __name__ == "__main__":
    run()
