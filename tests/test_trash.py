"""سلة المحذوفات والحذف النهائي والحذف التلقائي وصلاحيات admin/manager وبطاقة بيانات المستخدم الجديد."""
import re, sys, pathlib, time, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_e2e as T
from test_e2e import app, tok, login, connect
from app import services, storage, config
from app.security import hash_pw, enc, audit_verify
import importlib; D = importlib.import_module("app.db")
NOW = lambda: int(time.time())


def mk_user(db, un, role, dept, pw):
    db.execute("INSERT INTO users(username,full_name,pw_hash,role,dept_id,must_change_pw,created_at) VALUES(?,?,?,?,?,0,?)", (un, un, hash_pw(pw), role, dept, NOW()))


def mk_event(db, dept, title, with_certs=0, deleted_ago_days=None):
    """فعالية مع مشاركين وشهادات وملفات PDF مشفّرة (ملف وهمي) ورسالة outbox لكل شهادة."""
    eid = db.insert("INSERT INTO events(dept_id,title_ar,template_id,event_date,reg_token,created_at,status,deleted_at) VALUES(?,?,?,?,?,?,?,?)",
                    (dept, title, 1, "2026-05-01", "tok" + title.replace(" ", "")[:10] + str(NOW()) + str(dept) + str(id(title) % 999), NOW(), "issued",
                     None if deleted_ago_days is None else NOW() - int(deleted_ago_days * 86400)))
    for i in range(with_certs):
        pid = db.insert("INSERT INTO participants(event_id,name_enc,created_at,dedupe,status,academic_status,gender,nationality) VALUES(?,?,?,?,?,?,?,?)",
                        (eid, enc(f"اسم {title} {i}"), NOW(), f"d{eid}-{i}", "approved", "student", "male", "omani"))
        serial = f"ZZ-{eid}-{i:03d}"
        db.insert("INSERT INTO certificates(participant_id,event_id,serial,verify_code,name_enc,issued_at,file) VALUES(?,?,?,?,?,?,?)",
                  (pid, eid, serial, f"VC{eid:04d}{i:04d}XYZ", enc(f"اسم {i}"), NOW(), storage.filename(serial)))
        storage.certs.save(serial, b"fake-pdf-bytes")
        (config.DATA / "outbox" / f"{serial}.eml").write_bytes(b"eml")
    return eid


def counts(db, eid):
    q = lambda t, col="event_id": db.execute(f"SELECT COUNT(*) FROM {t} WHERE {col}=?", (eid,)).fetchone()[0]
    return dict(ev=db.execute("SELECT COUNT(*) FROM events WHERE id=?", (eid,)).fetchone()[0], part=q("participants"), cert=q("certificates"))


def files_of(db, eid):
    return [r[0] for r in db.execute("SELECT serial FROM certificates WHERE event_id=?", (eid,)).fetchall()]


def chain_ok(db, after=2):
    """يتحقق من سلسلة التدقيق بعد السجل رقم `after` (اختبار e2e يعبث عمداً بالسجل 2 لاختبار كشف التلاعب)."""
    import hashlib, json
    prev = None
    for r in db.execute("SELECT * FROM audit WHERE id>=? ORDER BY id", (after + 1,)):
        if prev is not None:
            payload = json.dumps([r["ts"], r["user_id"], r["username"], r["ip"], r["action"], r["target"], r["detail"], prev], ensure_ascii=False)
            if r["prev_hash"] != prev or hashlib.sha256(payload.encode()).hexdigest() != r["hash"]: return False
        prev = r["hash"]
    return True


def run():
    T.test_all()                                   # بيانات حقيقية: قسمان، admin، org1 (قسم 2)، view1 (قسم 1)، فعالية بشهادات صادرة في القسم 1
    db = connect()
    eid = db.execute("SELECT id FROM events WHERE dept_id=1 ORDER BY id LIMIT 1").fetchone()[0]
    title = db.execute("SELECT title_ar FROM events WHERE id=?", (eid,)).fetchone()[0]
    n_before = counts(db, eid); assert n_before["cert"] > 0 and n_before["part"] > 0, n_before
    serials = files_of(db, eid); assert all(storage.certs.exists(s) for s in serials)
    cid = db.execute("SELECT id FROM certificates WHERE event_id=? LIMIT 1", (eid,)).fetchone()[0]
    code = db.execute("SELECT verify_code FROM certificates WHERE event_id=? LIMIT 1", (eid,)).fetchone()[0]
    mk_user(db, "mgr1", "manager", 1, "Mgr1-Pass-123456"); mk_user(db, "mgr2", "manager", 2, "Mgr2-Pass-123456")
    mk_user(db, "org_d1", "organizer", 1, "OrgD1-Pass-123456"); mk_user(db, "view_d1", "viewer", 1, "ViewD1-Pass-123456")
    admin, m1, m2, org, view, anon = (app.test_client() for _ in range(6))
    assert login(admin, "admin", "Admin-Pass-12345").status_code == 302
    assert login(m1, "mgr1", "Mgr1-Pass-123456").status_code == 302 and login(m2, "mgr2", "Mgr2-Pass-123456").status_code == 302
    assert login(org, "org_d1", "OrgD1-Pass-123456").status_code == 302 and login(view, "view_d1", "ViewD1-Pass-123456").status_code == 302
    P = lambda cl, url, data=None, page="/": cl.post(url, data=dict(data or {}, _csrf=tok(cl, page)))

    # ---- 1) صلاحيات الحذف: النطاق والدور وCSRF
    assert m2.post(f"/events/{eid}/delete", data=dict(_csrf=tok(m2, "/"))).status_code == 404, "رئيس قسم آخر حذف فعالية ليست من قسمه"
    assert anon.post(f"/events/{eid}/delete").status_code in (400, 302, 401, 403)
    assert m1.post(f"/events/{eid}/delete").status_code in (400, 403), "حذف بلا CSRF"
    for who in (org, view):                       # منظّم ومطّلع من نفس القسم: لا حذف ولا سلة ولا حذف نهائي (فحص في الخادم لا في الواجهة)
        assert who.post(f"/events/{eid}/delete", data=dict(_csrf=tok(who, "/"))).status_code == 403
        assert who.post(f"/events/{eid}/restore", data=dict(_csrf=tok(who, "/"))).status_code == 403
        assert who.post(f"/trash/{eid}/purge", data=dict(_csrf=tok(who, "/"), confirm="yes")).status_code == 403
        assert who.get("/trash").status_code == 403 and who.get(f"/trash/{eid}/purge").status_code == 403
        assert "سلة المحذوفات" not in who.get("/events").get_data(True)
    assert db.execute("SELECT deleted_at FROM events WHERE id=?", (eid,)).fetchone()[0] is None
    # رسالة التأكيد الواضحة تظهر في صفحة الفعالية وتذكر الشهادات الصادرة (الحذف بعد الإصدار مسموح الآن)
    page = m1.get(f"/events/{eid}").get_data(True)
    assert "سلة المحذوفات" in page and "شهادة" in page and f'action="/events/{eid}/delete"' in page

    # ---- 2) الحذف العادي بعد إصدار الشهادات = نقل إلى السلة بلا فقد بيانات
    stats_before = admin.get("/stats").get_data(True); dash_before = admin.get("/").get_data(True)
    r = P(m1, f"/events/{eid}/delete"); assert r.status_code == 302, r.status_code
    row = db.execute("SELECT deleted_at, deleted_by, reg_open FROM events WHERE id=?", (eid,)).fetchone()
    assert row[0] and row[1] and row[2] == 0
    assert counts(db, eid) == n_before, "الحذف العادي لا يجوز أن يمسح بيانات"
    assert all(storage.certs.exists(s) for s in serials)
    assert db.execute("SELECT 1 FROM audit WHERE action='event.delete' AND target=?", (f"event:{eid}",)).fetchone()

    # ---- 3) المحذوفة لا تظهر في أي مكان: الفعاليات، لوحة التحكم، الأرشيف، البحث، الإحصاءات، الروابط العامة، الشهادات
    assert title not in admin.get("/events").get_data(True)
    assert title not in admin.get("/events", query_string={"q": title[:5]}).get_data(True)
    assert admin.get(f"/events/{eid}").status_code == 404 and m1.get(f"/events/{eid}/edit").status_code == 404
    assert admin.get(f"/certs/{cid}.pdf").status_code == 404
    ar = admin.get("/archive", query_string={"q": serials[0]}).get_data(True); assert f"/certs/{cid}.pdf" not in ar and title not in ar, "الأرشيف العادي يعرض شهادة فعالية محذوفة"
    assert title not in admin.get("/search", query_string={"q": title[:6]}).get_data(True)
    j = admin.get("/api/search", query_string={"q": title[:6]}).get_json(); assert not j["events"] and not j["people"], j
    sj = admin.get("/stats").get_data(True); assert sj != stats_before, "الإحصاءات لم تتأثر بحذف فعالية صادرة"
    assert admin.get(f"/v/{code}").status_code in (200, 404) and title not in admin.get(f"/v/{code}").get_data(True)
    tokr = db.execute("SELECT reg_token FROM events WHERE id=?", (eid,)).fetchone()[0]
    assert anon.get(f"/r/{tokr}").status_code == 404
    tot = db.execute("SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE e.deleted_at IS NULL").fetchone()[0]
    assert tot == 0 or admin.get("/archive").status_code == 200

    # ---- 4) صفحة السلة: عزل الأقسام والأدوار
    assert title in m1.get("/trash").get_data(True) and title in admin.get("/trash").get_data(True)
    assert title not in m2.get("/trash").get_data(True), "رئيس قسم آخر يرى محذوفات غيره"
    for path in (f"/events/{eid}/restore", f"/trash/{eid}/purge"):
        assert m2.post(path, data=dict(_csrf=tok(m2, "/trash"))).status_code == 404, path
        assert anon.post(path).status_code in (400, 302, 401, 403)
    assert m2.get(f"/trash/{eid}/purge").status_code == 404
    assert counts(db, eid) == n_before

    # ---- 5) الاستعادة ثم الحذف ثانية
    r = P(m1, f"/events/{eid}/restore", page="/trash"); assert r.status_code == 302
    assert db.execute("SELECT deleted_at FROM events WHERE id=?", (eid,)).fetchone()[0] is None and title in admin.get("/events").get_data(True)
    assert admin.get(f"/trash/{eid}/purge").status_code == 404, "لا حذف نهائي لفعالية غير محذوفة"
    assert admin.post(f"/trash/{eid}/purge", data=dict(_csrf=tok(admin, "/"), confirm="yes")).status_code == 404 and counts(db, eid) == n_before
    assert P(admin, f"/events/{eid}/delete").status_code == 302           # المشرف العام يحذف أي قسم

    # ---- 6) الحذف النهائي: تأكيد ثانٍ إلزامي + حذف كل البيانات والملفات + رسالة نجاح
    pg = m1.get(f"/trash/{eid}/purge"); assert pg.status_code == 200 and "لا يمكن التراجع" in pg.get_data(True) and str(n_before["cert"]) in pg.get_data(True)
    r = P(m1, f"/trash/{eid}/purge", page=f"/trash/{eid}/purge"); assert r.status_code == 302 and counts(db, eid) == n_before, "حذف بلا تأكيد صريح"
    assert m1.post(f"/trash/{eid}/purge", data=dict(confirm="yes")).status_code in (400, 403) and counts(db, eid) == n_before, "حذف نهائي بلا CSRF"
    r = P(m1, f"/trash/{eid}/purge", dict(confirm="yes"), page=f"/trash/{eid}/purge"); assert r.status_code == 302
    ok = m1.get("/trash").get_data(True); assert "تم الحذف النهائي" in ok and title in ok, "رسالة النجاح غير ظاهرة"
    assert counts(db, eid) == dict(ev=0, part=0, cert=0), counts(db, eid)
    assert not any(storage.certs.exists(s) for s in serials), "ملفات الشهادات لم تُحذف"
    assert not any((config.DATA / "outbox" / f"{s}.eml").exists() for s in serials)
    assert db.execute("SELECT COUNT(*) FROM participants WHERE event_id NOT IN (SELECT id FROM events)").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM certificates WHERE event_id NOT IN (SELECT id FROM events)").fetchone()[0] == 0
    a = db.execute("SELECT detail FROM audit WHERE action='event.purge' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "participants=" in a and "اسم" not in a.replace(title, ""), "سجل التدقيق يجب ألا يحوي بيانات شخصية"
    assert chain_ok(db), "سلسلة التدقيق انكسرت"

    # ---- 7) الحذف التلقائي بعد المدة (7 افتراضياً) وقابلية تعديل المدة
    e_old = mk_event(db, 1, "قديمة جداً", with_certs=2, deleted_ago_days=8)
    e_mid = mk_event(db, 1, "متوسطة", with_certs=2, deleted_ago_days=5)
    e_new = mk_event(db, 2, "جديدة", with_certs=1, deleted_ago_days=1)
    e_live = mk_event(db, 1, "فعّالة", with_certs=1)
    s_old, s_mid = files_of(db, e_old), files_of(db, e_mid)
    assert services.trash_days(db) == 7
    assert services.purge_expired_trash(db) == 1
    assert counts(db, e_old) == dict(ev=0, part=0, cert=0) and not any(storage.certs.exists(s) for s in s_old)
    assert counts(db, e_mid)["cert"] == 2 and counts(db, e_new)["cert"] == 1 and counts(db, e_live)["cert"] == 1, "حُذف ما لم تنتهِ مدته"
    r = P(admin, "/admin/settings", dict(trash_days="3", email_subject="s", email_body="b", token_days="90", verify_name_mode="masked"), page="/admin/settings")
    assert r.status_code == 302 and services.trash_days(db) == 3
    assert services.purge_expired_trash(db) == 1 and counts(db, e_mid)["ev"] == 0 and not any(storage.certs.exists(s) for s in s_mid)
    assert counts(db, e_new)["ev"] == 1 and counts(db, e_live)["ev"] == 1
    # الدالة الدورية (مطالبة ذرّية + حد زمني): تعمل عند انتهاء الفاصل فقط
    db.execute("UPDATE events SET deleted_at=? WHERE id=?", (NOW() - 4 * 86400, e_new))
    D.set_setting(db, "trash_last_purge", str(NOW() - 10)); assert services.maybe_purge(db) == 0 and counts(db, e_new)["ev"] == 1, "لم يحترم الفاصل الزمني"
    D.set_setting(db, "trash_last_purge", str(NOW() - 7200)); assert services.maybe_purge(db) == 1 and counts(db, e_new)["ev"] == 0
    assert chain_ok(db)

    # ---- 8) رئيس قسم يحذف فعاليات قسمه فقط (حتى بعد الإصدار)، والمشرف العام كل الأقسام
    e2 = mk_event(db, 2, "قسم اثنين", with_certs=2); e1 = mk_event(db, 1, "قسم واحد", with_certs=1)
    assert m2.post(f"/events/{e1}/delete", data=dict(_csrf=tok(m2, "/"))).status_code == 404
    assert P(m2, f"/events/{e2}/delete").status_code == 302 and db.execute("SELECT deleted_at FROM events WHERE id=?", (e2,)).fetchone()[0]
    assert P(m2, f"/trash/{e2}/purge", dict(confirm="yes"), page=f"/trash/{e2}/purge").status_code == 302 and counts(db, e2)["ev"] == 0
    assert db.execute("SELECT deleted_at FROM events WHERE id=?", (e1,)).fetchone()[0] is None

    # ---- 9) بطاقة بيانات المستخدم الجديد: عرض مرة واحدة + نسخ + رسالة جاهزة + إجبار تغيير كلمة المرور
    for role, dept in (("admin", ""), ("manager", "1"), ("organizer", "2")):
        un = f"new_{role}"
        r = admin.post("/admin/users", data=dict(_csrf=tok(admin, "/admin/users"), act="create", username=un, full_name=f"فلان {role}", role=role, dept_id=dept))
        h = r.get_data(True); assert r.status_code == 200 and 'class="card cred"' in h, role
        pw = re.search(r'ltr mono">([^<]+)<', h).group(1)
        assert h.count("data-copy=") == 3 and f'data-copy="{un}"' in h and f'data-copy="{pw}"' in h
        msg = re.search(r'<textarea[^>]*>(.*?)</textarea>', h, re.S).group(1)
        import html as H; msg = H.unescape(msg)
        assert un in msg and pw in msg and "تغيير كلمة المرور" in msg and "/login" in msg and ("رئيس قسم" in msg if role == "manager" else True)
        assert f'data-copy="{H.escape(msg)}"' in h, "زر نسخ الرسالة كاملة لا يحمل الرسالة نفسها"
        assert pw not in admin.get("/admin/users").get_data(True), "كلمة المرور ظهرت ثانية"
        nu = app.test_client(); rr = login(nu, un, pw); assert rr.status_code == 302
        assert "/account" in nu.get("/").headers.get("Location", "") or nu.get("/events").status_code in (302, 303), "لم يُجبر على تغيير كلمة المرور"
    pagu = admin.get("/admin/users").get_data(True)
    assert "رئيس قسم" in pagu and "مدير قسم" not in pagu
    # إعادة تعيين كلمة المرور تعرض البطاقة أيضاً
    uid = db.execute("SELECT id FROM users WHERE username='new_manager'").fetchone()[0]
    h = admin.post("/admin/users", data=dict(_csrf=tok(admin, "/admin/users"), act="reset", uid=str(uid))).get_data(True)
    assert 'class="card cred"' in h and h.count("data-copy=") == 3
    # الصلاحيات الحالية بلا تغيير: رئيس القسم لا يدخل إدارة المستخدمين ولا الإعدادات
    assert m1.get("/admin/users").status_code in (403, 404) and m1.get("/admin/settings").status_code in (403, 404)
    print("TRASH OK")


if __name__ == "__main__":
    run()
