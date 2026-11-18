"""سياق الواجهة المشترك: الإشعارات، مسار التنقل، صلاحية الذكاء."""
import time
from flask import g, request, url_for
from .config import Cfg
from .db import db, setting
from .security import dept_scope

PARENTS = {
    "main.event_new": [("الفعاليات", "main.events")], "main.event_view": [("الفعاليات", "main.events")],
    "main.event_edit": [("الفعاليات", "main.events"), ("EV", "main.event_view")], "main.import_view": [("الفعاليات", "main.events"), ("EV", "main.event_view")],
    "main.event_qr": [("الفعاليات", "main.events"), ("EV", "main.event_view")], "main.job_page": [("الفعاليات", "main.events"), ("EV", "main.event_view")],
    "main.flow": [("الفعاليات", "main.events"), ("EV", "main.event_view")], "reports.person": [("الأرشيف", "reports.archive")],
    "admin.template_edit": [("الإدارة", None), ("قوالب الشهادات", "admin.templates")], "admin.users": [("الإدارة", None)], "admin.departments": [("الإدارة", None)],
    "admin.settings": [("الإدارة", None)], "admin.audit_log": [("الإدارة", None)], "admin.templates": [("الإدارة", None)],
    "ai.assistant": [("الذكاء", None)], "ai.designer": [("الذكاء", None)], "ai.insights": [("الذكاء", None)], "ai.data": [("الذكاء", None)],
}


def ai_allowed():
    return bool(g.get("user")) and g.user["role"] != "viewer" and setting(db(), "ai_enabled", "1") == "1"


def notifications():
    c = db(); s = dept_scope(); w, a = (" AND e.deleted_at IS NULL", [])
    if s is not None: w, a = " AND e.dept_id=? AND e.deleted_at IS NULL", [s]
    out = []
    r = c.execute(f"SELECT e.id, e.title_ar, COUNT(*) n FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='pending'{w} GROUP BY e.id ORDER BY n DESC", a).fetchall()
    for x in r[:3]:
        out.append(dict(level="warn", text=f"{x['n']} تسجيل بانتظار المراجعة — {x['title_ar']}", url=url_for("main.flow", eid=x["id"], step=3)))
    r = c.execute(f"""SELECT e.id, e.title_ar, COUNT(*) n FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='approved'{w}
        AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid') GROUP BY e.id ORDER BY n DESC""", a).fetchall()
    for x in r[:3]:
        out.append(dict(level="info", text=f"{x['n']} معتمد بانتظار إصدار الشهادة — {x['title_ar']}", url=url_for("main.flow", eid=x["id"], step=6)))
    r = c.execute(f"SELECT e.id, e.title_ar, COUNT(*) n FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.email_status='failed' AND x.status='valid'{w} GROUP BY e.id", a).fetchall()
    for x in r[:3]:
        out.append(dict(level="err", text=f"فشل إرسال {x['n']} بريد — {x['title_ar']}", url=url_for("main.flow", eid=x["id"], step=7)))
    for x in c.execute(f"SELECT e.id, e.title_ar FROM events e WHERE e.reg_open=1{w} LIMIT 3", a):
        out.append(dict(level="ok", text=f"التسجيل بالـ QR مفتوح — {x['title_ar']}", url=url_for("main.event_qr", eid=x["id"])))
    if g.user["role"] == "admin":
        n = c.execute("SELECT COUNT(*) FROM users WHERE locked_until>?", (int(time.time()),)).fetchone()[0]
        if n: out.append(dict(level="err", text=f"{n} حساب مقفل بعد محاولات دخول فاشلة", url=url_for("admin.users")))
        if not g.user["totp_secret"]: out.append(dict(level="warn", text="فعّل التحقق بخطوتين لحسابك", url=url_for("auth.account")))
        if Cfg.MAIL_MODE == "file": out.append(dict(level="warn", text="البريد في وضع الاختبار: لا تُرسل الرسائل فعلياً", url=url_for("admin.settings")))
    return out


def context():
    if not g.get("user"):
        return {}
    ep = request.endpoint or ""
    crumbs = []
    va = request.view_args or {}
    eid = va.get("eid")
    if eid is None and "jid" in va:  # صفحة المهمة تحمل jid فنستخرج منها الفعالية
        r = db().execute("SELECT event_id FROM jobs WHERE id=?", (va["jid"],)).fetchone()
        eid = r[0] if r else None
    for label, tgt in PARENTS.get(ep, []):
        url = None
        if tgt:
            if label == "EV":
                # لا نعرض عنوان فعالية خارج نطاق صلاحية المستخدم (صفحات الرفض 403/404 تمر من هنا أيضاً)
                row = db().execute("SELECT title_ar, dept_id FROM events WHERE id=?", (eid,)).fetchone() if eid else None
                s_ = dept_scope()
                if row and (s_ is None or s_ == row["dept_id"]):
                    label = row["title_ar"]; url = url_for(tgt, eid=eid)
                else:
                    label = "الفعالية"
            else:
                url = url_for(tgt)
        crumbs.append((label, url))
    notes = notifications() if "json" not in request.path else []
    return dict(crumbs=crumbs, notes=notes, ai_on=ai_allowed())
