"""البحث العام وواجهات مساعدة للشريط العلوي."""
from flask import Blueprint, g, jsonify, render_template, request, url_for
from .db import db
from .security import *
from .util import *
from . import views_reports as vr

bp = Blueprint("ui", __name__)

PAGES = [("الرئيسية", "main.dashboard", "dashboard لوحة"), ("الفعاليات", "main.events", "events فعاليات دورات"), ("فعالية جديدة", "main.event_new", "new create إنشاء"),
         ("الأرشيف", "reports.archive", "archive سجل"), ("الإحصائيات", "reports.stats", "stats تقارير احصائيات"), ("حسابي", "auth.account", "account كلمة المرور 2fa"),
         ("صفحة التحقق", "public.verify_form", "verify تحقق"), ("مساعد إنجاز", "ai.assistant", "ai ذكاء مساعد"), ("محلل البيانات", "ai.data", "ai excel تحليل بيانات"),
         ("مصمم الشهادات", "ai.designer", "ai تصميم ألوان"), ("التحليلات الذكية", "ai.insights", "ai تقرير ملخص")]
ADMIN_PAGES = [("قوالب الشهادات", "admin.templates", "templates قوالب تصميم"), ("المستخدمون", "admin.users", "users مستخدمين"), ("الأقسام والمراكز", "admin.departments", "departments"),
               ("الإعدادات", "admin.settings", "settings"), ("سجل التدقيق", "admin.audit_log", "audit")]


@bp.route("/api/search")
@login_required
def api_search():
    q = clean_text(request.args.get("q"), 60)
    if len(q) < 2: return jsonify(pages=[], events=[], people=[])
    ql = q.lower(); c = db(); s = dept_scope()
    pages = [dict(t=t, u=url_for(e)) for t, e, kw in PAGES + (ADMIN_PAGES if g.user["role"] == "admin" else []) if ql in t.lower() or ql in kw.lower()][:4]
    w, a = (" AND deleted_at IS NULL", [])
    if s is not None: w, a = " AND dept_id=? AND deleted_at IS NULL", [s]
    evs = c.execute(f"SELECT id,title_ar,event_date FROM events WHERE (title_ar LIKE ? OR title_en LIKE ?){w} ORDER BY event_date DESC LIMIT 5", (f"%{q}%", f"%{q}%", *a)).fetchall()
    events = [dict(t=e["title_ar"], d=e["event_date"], u=url_for("main.event_view", eid=e["id"])) for e in evs]
    rows = vr._search(dict(q=q, dept="", year="", cat="", status="", spec="", dfrom="", dto=""), limit=6)
    people = [dict(t=dec(r["name_enc"]), d=r["serial"], u=url_for("reports.archive", q=r["serial"])) for r in rows[:5]]
    if people: audit("search.global", "", q[:40])
    return jsonify(pages=pages, events=events, people=people)


@bp.route("/search")
@login_required
def search():
    q = clean_text(request.args.get("q"), 60)
    ev, rows = [], []
    if q:
        w, a = (" AND e.deleted_at IS NULL", [])
        if dept_scope() is not None: w, a = " AND e.dept_id=? AND e.deleted_at IS NULL", [dept_scope()]
        ev = db().execute(f"SELECT e.*, d.name_ar dept_ar FROM events e JOIN departments d ON d.id=e.dept_id WHERE (e.title_ar LIKE ? OR e.title_en LIKE ?){w} ORDER BY e.event_date DESC LIMIT 20", (f"%{q}%", f"%{q}%", *a)).fetchall()
        rows = [dict(r, name=dec(r["name_enc"]), uid=dec(r["uid_enc"])) for r in vr._search(dict(q=q, dept="", year="", cat="", status="", spec="", dfrom="", dto=""), limit=30)]
        audit("search.global", "", q[:40])
    return render_template("search.html", q=q, ev=ev, rows=rows)
