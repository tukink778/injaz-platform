import io, json, re, secrets, time, zipfile
import qrcode, qrcode.image.svg
from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for)
from pypdf import PdfWriter, PdfReader
from . import certs, services
from .config import Cfg
from .db import db, setting
from .security import *
from .util import *

bp = Blueprint("main", __name__)


def _events_scope(alias="e"):
    s = dept_scope()
    return (f" AND {alias}.dept_id=?", [s]) if s is not None else ("", [])


ACTION_AR = {"event.create": "أنشأ فعالية", "event.edit": "عدّل فعالية", "event.delete": "حذف فعالية", "certs.issue": "أصدر شهادات", "import.confirm": "استورد مشاركين من Excel",
             "cert.revoke": "ألغى شهادة", "cert.resend": "أعاد إرسال شهادة", "reg.toggle": "غيّر حالة التسجيل", "part.add": "أضاف مشاركاً", "part.approve_all": "اعتمد المسجلين",
             "part.approve": "اعتمد مشاركين", "part.reject": "رفض مشاركين", "template.create": "أنشأ قالباً", "template.edit": "عدّل قالباً", "template.delete": "حذف قالباً",
             "template.duplicate": "نسخ قالباً", "user.create": "أضاف مستخدماً", "login": "سجّل الدخول", "mail.send": "أرسل شهادات بالبريد", "cert.zip": "صدّر شهادات ZIP"}


@bp.route("/")
@login_required
def dashboard():
    c = db(); w, a = _events_scope()
    k = {}
    k["events"] = c.execute(f"SELECT COUNT(*) FROM events e WHERE 1=1{w}", a).fetchone()[0]
    k["active"] = c.execute(f"SELECT COUNT(*) FROM events e WHERE e.status IN ('draft','open'){w}", a).fetchone()[0]
    k["done"] = c.execute(f"SELECT COUNT(*) FROM events e WHERE e.status IN ('issued','closed'){w}", a).fetchone()[0]
    k["certs"] = c.execute(f"SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='valid'{w}", a).fetchone()[0]
    k["pending"] = c.execute(f"SELECT COUNT(*) FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='pending'{w}", a).fetchone()[0]
    k["unissued"] = c.execute(f"SELECT COUNT(*) FROM participants p JOIN events e ON e.id=p.event_id WHERE p.status='approved'{w} AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid')", a).fetchone()[0]
    k["unsent"] = c.execute(f"SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.status='valid' AND x.email_status!='sent'{w}", a).fetchone()[0]
    recent = c.execute(f"""SELECT e.*, d.name_ar dept_ar,
        (SELECT COUNT(*) FROM participants WHERE event_id=e.id) n_part,
        (SELECT COUNT(*) FROM certificates WHERE event_id=e.id AND status='valid') n_cert
        FROM events e JOIN departments d ON d.id=e.dept_id WHERE 1=1{w} ORDER BY e.id DESC LIMIT 6""", a).fetchall()
    months = c.execute(f"""SELECT strftime('%Y-%m', x.issued_at, 'unixepoch', '+4 hours') m, COUNT(*) n FROM certificates x JOIN events e ON e.id=x.event_id
        WHERE x.status='valid'{w} GROUP BY m ORDER BY m DESC LIMIT 6""", a).fetchall()[::-1]
    if g.user["role"] == "admin":
        acts = c.execute("SELECT ts, username, action, target, detail FROM audit WHERE action IN (%s) ORDER BY id DESC LIMIT 8" % ",".join("?" * len(ACTION_AR)), tuple(ACTION_AR)).fetchall()
    else:
        acts = c.execute("SELECT ts, username, action, target, detail FROM audit WHERE user_id=? AND action IN (%s) ORDER BY id DESC LIMIT 8" % ",".join("?" * len(ACTION_AR)), (g.user["id"], *ACTION_AR)).fetchall()
    # أقرب خطوة تالية: أول فعالية تحتاج إجراءً
    nxt = c.execute(f"""SELECT e.id, e.title_ar, (SELECT COUNT(*) FROM participants WHERE event_id=e.id AND status='pending') pend,
        (SELECT COUNT(*) FROM participants p WHERE event_id=e.id AND status='approved' AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid')) uniss
        FROM events e WHERE e.status!='closed'{w} ORDER BY (pend>0) DESC, (uniss>0) DESC, e.id DESC LIMIT 1""", a).fetchone()
    return render_template("dashboard.html", k=k, recent=recent, months=[(m, n) for m, n in months], acts=acts, ACTION_AR=ACTION_AR, nxt=nxt)



def _ret(eid, default=None):
    """بعد تنفيذ إجراء: العودة لخطوة المسار الإرشادي إن طُلب ذلك (مسار محلي محقق) وإلا لصفحة الفعالية."""
    n = request.form.get("next", "")
    if re.fullmatch(r"/events/\d+/flow\?step=[1-7]", n): return redirect(n)
    return redirect(default or url_for("main.event_view", eid=eid))


@bp.route("/events")
@login_required
def events():
    c = db(); w, a = _events_scope()
    q = clean_text(request.args.get("q"), 80); st = request.args.get("status", ""); yr = request.args.get("year", "")
    dept = request.args.get("dept", "")
    if q: w += " AND (e.title_ar LIKE ? OR e.title_en LIKE ?)"; a += [f"%{q}%"] * 2
    if st in STATUS_AR: w += " AND e.status=?"; a.append(st)
    if yr.isdigit(): w += " AND substr(e.event_date,1,4)=?"; a.append(yr)
    if dept.isdigit() and g.user["role"] == "admin": w += " AND e.dept_id=?"; a.append(int(dept))
    rows = c.execute(f"""SELECT e.*, d.name_ar dept_ar,
        (SELECT COUNT(*) FROM participants WHERE event_id=e.id) n_part,
        (SELECT COUNT(*) FROM certificates WHERE event_id=e.id AND status='valid') n_cert
        FROM events e JOIN departments d ON d.id=e.dept_id WHERE 1=1{w} ORDER BY e.event_date DESC, e.id DESC LIMIT 300""", a).fetchall()
    depts = c.execute("SELECT * FROM departments WHERE active=1 ORDER BY name_ar").fetchall()
    return render_template("events.html", rows=rows, depts=depts, q=q, st=st, yr=yr, dept=dept)


def _event_form(ev=None):
    c = db()
    tpls = c.execute("SELECT id,name FROM templates WHERE active=1 ORDER BY builtin DESC, id").fetchall()
    depts = c.execute("SELECT * FROM departments WHERE active=1 ORDER BY name_ar").fetchall()
    if request.method == "POST":
        f = request.form
        d = dict(title_ar=clean_text(f.get("title_ar"), 200), title_en=clean_text(f.get("title_en"), 200),
                 category=f.get("category") if f.get("category") in CATEGORIES else "training",
                 template_id=int(f.get("template_id") or 0), event_date=f.get("event_date", ""), end_date=f.get("end_date") or None,
                 location=clean_text(f.get("location"), 120), signatory_name=clean_text(f.get("signatory_name"), 80),
                 signatory_title_ar=clean_text(f.get("signatory_title_ar"), 80), signatory_title_en=clean_text(f.get("signatory_title_en"), 80),
                 reg_pin=clean_text(f.get("reg_pin"), 12), reg_opens_at=f.get("reg_opens_at") or None, reg_closes_at=f.get("reg_closes_at") or None,
                 auto_approve=1 if f.get("auto_approve") else 0, require_uid=1 if f.get("require_uid") else 0, require_spec=1 if f.get("require_spec") else 0)
        try:
            d["hours"] = max(0.0, min(1000.0, float(f.get("hours") or 0)))
            d["max_participants"] = max(0, min(100000, int(f.get("max_participants") or 0)))
            time.strptime(d["event_date"], "%Y-%m-%d")
            if d["end_date"]: time.strptime(d["end_date"], "%Y-%m-%d")
        except ValueError:
            flash("تحقق من التاريخ والأرقام", "err"); return None, tpls, depts
        if not d["title_ar"] or not c.execute("SELECT 1 FROM templates WHERE id=? AND active=1", (d["template_id"],)).fetchone():
            flash("اسم الفعالية والقالب مطلوبان", "err"); return None, tpls, depts
        if d["end_date"] and d["end_date"] < d["event_date"]:
            flash("تاريخ النهاية قبل البداية", "err"); return None, tpls, depts
        dept = int(f.get("dept_id") or 0) if g.user["role"] == "admin" else g.user["dept_id"]
        if not c.execute("SELECT 1 FROM departments WHERE id=?", (dept,)).fetchone(): abort(400)
        d["dept_id"] = dept
        return d, tpls, depts
    return None, tpls, depts


@bp.route("/events/new", methods=["GET", "POST"])
@roles("admin", "manager", "organizer")
def event_new():
    d, tpls, depts = _event_form()
    if d:
        c = db()
        cols = ",".join(d) + ",reg_token,created_by,created_at"
        c.execute(f"INSERT INTO events({cols}) VALUES({','.join('?'*(len(d)+3))})", (*d.values(), secrets.token_urlsafe(16), g.user["id"], int(time.time())))
        eid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit("event.create", f"event:{eid}", d["title_ar"])
        flash("تم إنشاء الفعالية. أضف المشاركين الآن", "ok")
        return redirect(url_for("main.flow", eid=eid, step=2))
    return render_template("event_form.html", ev=None, tpls=tpls, depts=depts, cats=CATEGORIES)


@bp.route("/events/<int:eid>/edit", methods=["GET", "POST"])
@roles("admin", "manager", "organizer")
def event_edit(eid):
    ev = event_or_404(eid)
    d, tpls, depts = _event_form(ev)
    if d:
        sets = ",".join(f"{k}=?" for k in d)
        db().execute(f"UPDATE events SET {sets} WHERE id=?", (*d.values(), eid))
        audit("event.edit", f"event:{eid}")
        flash("تم حفظ التعديلات", "ok")
        return _ret(eid)
    return render_template("event_form.html", ev=ev, tpls=tpls, depts=depts, cats=CATEGORIES)


@bp.route("/events/<int:eid>/delete", methods=["POST"])
@roles("admin", "manager")
def event_delete(eid):
    ev = event_or_404(eid)
    if db().execute("SELECT 1 FROM certificates WHERE event_id=?", (eid,)).fetchone():
        flash("لا يمكن حذف فعالية صدرت لها شهادات (حفاظاً على الأرشيف). يمكنك إغلاقها", "err")
        return redirect(url_for("main.event_view", eid=eid))
    db().execute("DELETE FROM events WHERE id=?", (eid,)); audit("event.delete", f"event:{eid}", ev["title_ar"])
    flash("تم حذف الفعالية", "ok"); return redirect(url_for("main.events"))


@bp.route("/events/<int:eid>")
@login_required
def event_view(eid):
    ev = event_or_404(eid); c = db()
    flt = request.args.get("s", "")
    rows = c.execute("""SELECT p.*, x.id cid, x.serial, x.status cstatus, x.email_status, x.email_error FROM participants p
        LEFT JOIN certificates x ON x.participant_id=p.id AND x.id=(SELECT MAX(id) FROM certificates WHERE participant_id=p.id)
        WHERE p.event_id=? ORDER BY p.id""", (eid,)).fetchall()
    parts = []
    for r in rows:
        v = part_view(r); v.update(cid=r["cid"], serial=r["serial"], cstatus=r["cstatus"], email_status=r["email_status"], email_error=r["email_error"])
        if flt and v["status"] != flt: continue
        parts.append(v)
    cnt = dict(c.execute("SELECT status, COUNT(*) FROM participants WHERE event_id=? GROUP BY status", (eid,)).fetchall())
    ncert = c.execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND status='valid'", (eid,)).fetchone()[0]
    nsent = c.execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND status='valid' AND email_status='sent'", (eid,)).fetchone()[0]
    to_issue = c.execute("SELECT COUNT(*) FROM participants p WHERE event_id=? AND status='approved' AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid')", (eid,)).fetchone()[0]
    tpl = c.execute("SELECT name FROM templates WHERE id=?", (ev["template_id"],)).fetchone()
    return render_template("event.html", ev=ev, parts=parts, cnt=cnt, ncert=ncert, nsent=nsent, to_issue=to_issue, flt=flt,
                           tpl_name=tpl["name"] if tpl else "—", reg_url=f"{Cfg.BASE_URL}/r/{ev['reg_token']}",
                           mail_ok=services.mail_configured(), mail_mode=Cfg.MAIL_MODE)


# ---------- المشاركون ----------
def _add_participant(c, ev, name, name_en, uid, email, spec, source, status, acad="", gender="", nat=""):
    key = blind("u", uid) if uid else blind("e", email)
    c.execute("INSERT INTO participants(event_id,name_enc,name_en_enc,uid_enc,uid_idx,email_enc,email_idx,specialization,academic_status,gender,nationality,source,status,checked_in_at,created_at,dedupe) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (ev["id"], enc(name), enc(name_en), enc(uid), blind("u", uid), enc(email), blind("e", email), spec, acad, gender, nat, source, status,
               int(time.time()) if source == "qr" else None, int(time.time()), key))


@bp.route("/events/<int:eid>/participants/add", methods=["POST"])
@roles("admin", "manager", "organizer")
def part_add(eid):
    ev = event_or_404(eid); f = request.form
    good, bad = services.validate_rows([form_row(f)], ev, _existing(ev["id"]), strict=True)
    if bad: flash("، ".join(bad[0]["errors"]), "err")
    else:
        r = good[0]
        _add_participant(db(), ev, r["name"], r["name_en"], r["uid"], r["email"], r["spec"], "manual", "approved", r["acad"], r["gender"], r["nat"]); audit("part.add", f"event:{eid}")
        flash("تمت إضافة المشارك", "ok")
    return _ret(eid)


def _existing(eid):
    return {r[0] for r in db().execute("SELECT dedupe FROM participants WHERE event_id=?", (eid,))}


@bp.route("/events/<int:eid>/participants/bulk", methods=["POST"])
@roles("admin", "manager", "organizer")
def part_bulk(eid):
    ev = event_or_404(eid)
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    act = request.form.get("act")
    if not ids: flash("لم تحدد أي مشارك", "warn"); return _ret(eid)
    c = db(); q = ",".join("?" * len(ids))
    if act in ("approve", "reject"):
        c.execute(f"UPDATE participants SET status=? WHERE event_id=? AND id IN ({q}) AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=participants.id AND x.status='valid')",
                  ("approved" if act == "approve" else "rejected", eid, *ids))
        audit("part." + act, f"event:{eid}", f"n={len(ids)}")
    elif act == "delete":
        c.execute(f"DELETE FROM participants WHERE event_id=? AND id IN ({q}) AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=participants.id)", (eid, *ids))
        audit("part.delete", f"event:{eid}", f"n={len(ids)}")
    else: abort(400)
    if request.form.get("act") == "approve" and ev["status"] == "issued":
        c.execute("UPDATE events SET status='closed' WHERE id=?", (eid,))
    flash("تم التنفيذ", "ok")
    return _ret(eid)


@bp.route("/events/<int:eid>/approve-all", methods=["POST"])
@roles("admin", "manager", "organizer")
def approve_all(eid):
    event_or_404(eid)
    db().execute("UPDATE participants SET status='approved' WHERE event_id=? AND status='pending'", (eid,))
    audit("part.approve_all", f"event:{eid}"); flash("تم اعتماد جميع المسجلين", "ok")
    return _ret(eid)


@bp.route("/participants/<int:pid>/edit", methods=["POST"])
@roles("admin", "manager", "organizer")
def part_edit(pid):
    c = db()
    p = c.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    if not p: abort(404)
    ev = event_or_404(p["event_id"])
    if c.execute("SELECT 1 FROM certificates WHERE participant_id=? AND status='valid'", (pid,)).fetchone():
        flash("صدرت شهادة لهذا المشارك. ألغِ الشهادة أولاً ثم عدّل البيانات", "err"); return redirect(url_for("main.event_view", eid=ev["id"]))
    f = request.form
    ex = _existing(ev["id"]) - {p["dedupe"]}
    good, bad = services.validate_rows([form_row(f)], ev, ex, strict=True)
    if bad: flash("، ".join(bad[0]["errors"]), "err")
    else:
        r = good[0]
        c.execute("UPDATE participants SET name_enc=?,name_en_enc=?,uid_enc=?,uid_idx=?,email_enc=?,email_idx=?,specialization=?,academic_status=?,gender=?,nationality=?,dedupe=? WHERE id=?",
                  (enc(r["name"]), enc(r["name_en"]), enc(r["uid"]), blind("u", r["uid"]), enc(r["email"]), blind("e", r["email"]), r["spec"], r["acad"], r["gender"], r["nat"], r["key"], pid))
        audit("part.edit", f"part:{pid}"); flash("تم الحفظ", "ok")
    return redirect(url_for("main.event_view", eid=ev["id"]))


# ---------- Excel ----------
@bp.route("/template.xlsx")
@login_required
def template_xlsx():
    return Response(services.build_template_xlsx(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=INJAZ-participants-template.xlsx"})


@bp.route("/events/<int:eid>/import", methods=["GET", "POST"])
@roles("admin", "manager", "organizer")
def import_view(eid):
    ev = event_or_404(eid)
    if request.method == "POST":
        fl = request.files.get("file")
        if not fl or not fl.filename.lower().endswith(".xlsx"):
            flash("ارفع ملف Excel بصيغة .xlsx", "err"); return redirect(request.url)
        data = fl.read(Cfg.MAX_UPLOAD + 1)
        if len(data) > Cfg.MAX_UPLOAD: flash("الملف أكبر من 5 ميجابايت", "err"); return redirect(request.url)
        try:
            rows = services.parse_xlsx(data)
        except services.ImportError_ as e:
            flash(str(e), "err"); return redirect(request.url)
        good, bad = services.validate_rows(rows, ev, _existing(eid))
        tok = services.stage_save(g.user["id"], good)
        audit("import.preview", f"event:{eid}", f"ok={len(good)} bad={len(bad)}")
        return render_template("import_preview.html", ev=ev, good=good, bad=bad, tok=tok, total=len(rows))
    return render_template("import.html", ev=ev)


@bp.route("/events/<int:eid>/import/confirm", methods=["POST"])
@roles("admin", "manager", "organizer")
def import_confirm(eid):
    ev = event_or_404(eid)
    tok = request.form.get("tok", "")
    good = services.stage_load(tok, g.user["id"])
    if good is None: flash("انتهت صلاحية المعاينة، أعد رفع الملف", "err"); return redirect(url_for("main.import_view", eid=eid))
    c = db(); n = 0; skipped = 0
    c.execute("BEGIN")
    try:
        for r in good:
            try:
                _add_participant(c, ev, r["name"], r["name_en"], r["uid"], r["email"], r["spec"], "excel", "approved", r.get("acad", ""), r.get("gender", ""), r.get("nat", "")); n += 1
            except Exception: skipped += 1
        c.execute("UPDATE events SET rows_total=rows_total+?, rows_invalid=rows_invalid+? WHERE id=?", (n + int(request.form.get("bad", 0) or 0) + skipped, int(request.form.get("bad", 0) or 0) + skipped, eid))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK"); raise
    services.stage_drop(tok); audit("import.confirm", f"event:{eid}", f"n={n}")
    flash(f"تم استيراد {n} مشارك" + (f" (تخطي {skipped})" if skipped else ""), "ok")
    return redirect(url_for("main.flow", eid=eid, step=3))


# ---------- QR التسجيل ----------
@bp.route("/events/<int:eid>/reg", methods=["POST"])
@roles("admin", "manager", "organizer")
def reg_toggle(eid):
    ev = event_or_404(eid)
    new = 0 if ev["reg_open"] else 1
    db().execute("UPDATE events SET reg_open=?, status=CASE WHEN status='draft' AND ?=1 THEN 'open' ELSE status END WHERE id=?", (new, new, eid))
    audit("reg.toggle", f"event:{eid}", str(new)); flash("فُتح التسجيل" if new else "أُغلق التسجيل", "ok")
    return _ret(eid)


@bp.route("/events/<int:eid>/reg/rotate", methods=["POST"])
@roles("admin", "manager", "organizer")
def reg_rotate(eid):
    event_or_404(eid)
    db().execute("UPDATE events SET reg_token=? WHERE id=?", (secrets.token_urlsafe(16), eid))
    audit("reg.rotate", f"event:{eid}"); flash("تم تغيير رابط وQR التسجيل. الرمز القديم لم يعد يعمل", "ok")
    return redirect(url_for("main.event_qr", eid=eid))


def _qr_png(url, box=10):
    img = qrcode.QRCode(box_size=box, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M); img.add_data(url); img.make(fit=True)
    b = io.BytesIO(); img.make_image(fill_color="#1b1f5e", back_color="white").save(b, "PNG"); return b.getvalue()


@bp.route("/events/<int:eid>/qr")
@login_required
def event_qr(eid):
    ev = event_or_404(eid)
    return render_template("event_qr.html", ev=ev, reg_url=f"{Cfg.BASE_URL}/r/{ev['reg_token']}")


@bp.route("/events/<int:eid>/qr.png")
@login_required
def event_qr_png(eid):
    ev = event_or_404(eid)
    return Response(_qr_png(f"{Cfg.BASE_URL}/r/{ev['reg_token']}"), mimetype="image/png")


# ---------- الإصدار والإرسال ----------
@bp.route("/events/<int:eid>/issue", methods=["POST"])
@roles("admin", "manager", "organizer")
def issue(eid):
    ev = event_or_404(eid); c = db()
    if request.form.get("pids"):
        ids = [int(i) for i in request.form.get("pids").split(",") if i.isdigit()]
    else:
        ids = [r[0] for r in c.execute("SELECT id FROM participants p WHERE event_id=? AND status='approved' AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid')", (eid,))]
    if not ids: flash("لا يوجد مشاركون معتمدون بانتظار الإصدار", "warn"); return _ret(eid)
    jid = services.start_job(current_app._get_current_object(), "issue", eid, g.user, len(ids),
                             lambda cc, j, u: services.issue_certs(cc, j, u, eid, ids))
    return redirect(url_for("main.job_page", jid=jid))


@bp.route("/events/<int:eid>/send", methods=["POST"])
@roles("admin", "manager", "organizer")
def send(eid):
    ev = event_or_404(eid); c = db()
    if not services.mail_configured(): flash("البريد غير مُعدّ على الخادم", "err"); return _ret(eid)
    only_failed = request.form.get("scope") != "all"
    q = "SELECT id FROM certificates WHERE event_id=? AND status='valid'" + ("" if not only_failed else " AND email_status!='sent'")
    ids = [r[0] for r in c.execute(q, (eid,))]
    if not ids: flash("لا توجد شهادات بانتظار الإرسال", "warn"); return _ret(eid)
    jid = services.start_job(current_app._get_current_object(), "send", eid, g.user, len(ids), lambda cc, j, u: services.send_job(cc, j, u, eid, ids))
    return redirect(url_for("main.job_page", jid=jid))


@bp.route("/jobs/<int:jid>")
@login_required
def job_page(jid):
    j = db().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not j: abort(404)
    ev = event_or_404(j["event_id"])
    return render_template("job.html", j=j, ev=ev)


@bp.route("/jobs/<int:jid>.json")
@login_required
def job_json(jid):
    j = db().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not j: abort(404)
    event_or_404(j["event_id"])
    return jsonify({k: j[k] for k in ("status", "total", "done", "failed", "message")})


def _cert_or_404(cid):
    x = db().execute("SELECT x.*, e.dept_id FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.id=?", (cid,)).fetchone()
    if not x: abort(404)
    check_dept(x["dept_id"]); return x


@bp.route("/certs/<int:cid>.pdf")
@login_required
def cert_pdf(cid):
    x = _cert_or_404(cid)
    audit("cert.view", x["serial"])
    return Response(services.read_cert_pdf(x), mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename={x['serial']}.pdf"})


@bp.route("/certs/<int:cid>/revoke", methods=["POST"])
@roles("admin", "manager")
def cert_revoke(cid):
    x = _cert_or_404(cid)
    reason = clean_text(request.form.get("reason"), 200) or "—"
    db().execute("UPDATE certificates SET status='revoked', revoked_at=?, revoked_reason=? WHERE id=?", (int(time.time()), reason, cid))
    db().execute("UPDATE events SET status='closed' WHERE id=? AND status='issued'", (x["event_id"],))
    audit("cert.revoke", x["serial"], reason); flash("أُلغيت الشهادة. ستظهر «ملغاة» عند التحقق منها", "ok")
    return redirect(request.referrer or url_for("main.event_view", eid=x["event_id"]))


@bp.route("/certs/<int:cid>/resend", methods=["POST"])
@roles("admin", "manager", "organizer")
def cert_resend(cid):
    x = _cert_or_404(cid)
    if x["status"] != "valid": abort(400)
    ev = event_or_404(x["event_id"])
    p = db().execute("SELECT * FROM participants WHERE id=?", (x["participant_id"],)).fetchone()
    try:
        services.send_cert_email(db(), x, dec(p["email_enc"]), dec(x["name_enc"]), ev, setting(db(), "attach_pdf", "0") == "1")
        db().execute("UPDATE certificates SET email_status='sent', email_sent_at=?, email_error='' WHERE id=?", (int(time.time()), cid))
        audit("cert.resend", x["serial"]); flash("أُعيد إرسال الشهادة", "ok")
    except Exception as e:
        flash(f"تعذر الإرسال: {type(e).__name__}", "err")
    return _ret(x["event_id"])


@bp.route("/events/<int:eid>/certs.zip")
@roles("admin", "manager", "organizer")
def event_zip(eid):
    ev = event_or_404(eid)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_STORED) as z:
        for x in db().execute("SELECT * FROM certificates WHERE event_id=? AND status='valid' ORDER BY id", (eid,)):
            z.writestr(f"{x['serial']}.pdf", services.read_cert_pdf(x))
    audit("cert.zip", f"event:{eid}"); b.seek(0)
    return send_file(b, mimetype="application/zip", as_attachment=True, download_name=f"certificates-event-{eid}.zip")


@bp.route("/events/<int:eid>/print.pdf")
@roles("admin", "manager", "organizer")
def event_print(eid):
    event_or_404(eid)
    w = PdfWriter()
    for x in db().execute("SELECT * FROM certificates WHERE event_id=? AND status='valid' ORDER BY id", (eid,)):
        for pg in PdfReader(io.BytesIO(services.read_cert_pdf(x))).pages: w.add_page(pg)
    b = io.BytesIO(); w.write(b); b.seek(0); audit("cert.print", f"event:{eid}")
    return send_file(b, mimetype="application/pdf", download_name=f"print-event-{eid}.pdf")


# ---------- المسار الإرشادي (7 خطوات) ----------
FLOW_STEPS = ["معلومات الفعالية", "المشاركون", "المراجعة والتحقق", "اختيار القالب", "المعاينة والاعتماد", "إصدار الشهادات", "الإرسال والأرشفة"]


def _flow_state(ev):
    c = db(); eid = ev["id"]
    cnt = dict(c.execute("SELECT status, COUNT(*) FROM participants WHERE event_id=? GROUP BY status", (eid,)).fetchall())
    total = sum(cnt.values())
    ncert = c.execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND status='valid'", (eid,)).fetchone()[0]
    nsent = c.execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND status='valid' AND email_status='sent'", (eid,)).fetchone()[0]
    nfail = c.execute("SELECT COUNT(*) FROM certificates WHERE event_id=? AND status='valid' AND email_status='failed'", (eid,)).fetchone()[0]
    to_issue = c.execute("SELECT COUNT(*) FROM participants p WHERE event_id=? AND status='approved' AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid')", (eid,)).fetchone()[0]
    done = [True, total > 0, cnt.get("approved", 0) > 0 and cnt.get("pending", 0) == 0, True, ncert > 0, ncert > 0 and to_issue == 0, ncert > 0 and nsent >= ncert]
    return dict(cnt=cnt, total=total, ncert=ncert, nsent=nsent, nfail=nfail, to_issue=to_issue, done=done)


@bp.route("/events/<int:eid>/flow")
@login_required
def flow(eid):
    ev = event_or_404(eid); c = db(); st = _flow_state(ev)
    try: step = int(request.args.get("step", 0))
    except ValueError: step = 0
    if not 1 <= step <= 7:
        step = next((i + 1 for i, d in enumerate(st["done"]) if not d), 7)
    ctx = dict(ev=ev, step=step, steps=FLOW_STEPS, st=st, cnt=st["cnt"], ncert=st["ncert"], nsent=st["nsent"], to_issue=st["to_issue"],
               mail_ok=services.mail_configured(), mail_mode=Cfg.MAIL_MODE, flow_next=f"/events/{eid}/flow?step={step}", flt=request.args.get("s", ""))
    if step in (3,):
        flt = ctx["flt"]
        rows = c.execute("""SELECT p.*, x.id cid, x.serial, x.status cstatus, x.email_status, x.email_error FROM participants p
            LEFT JOIN certificates x ON x.participant_id=p.id AND x.id=(SELECT MAX(id) FROM certificates WHERE participant_id=p.id)
            WHERE p.event_id=? ORDER BY p.id""", (eid,)).fetchall()
        parts = []
        for r in rows:
            v = part_view(r); v.update(cid=r["cid"], serial=r["serial"], cstatus=r["cstatus"], email_status=r["email_status"], email_error=r["email_error"])
            if flt and v["status"] != flt: continue
            parts.append(v)
        ctx["parts"] = parts
        # ملخص فحص الجودة
        allp = [part_view(r) for r in rows]
        miss_uid = sum(1 for p in allp if not p["uid"]) if ev["require_uid"] else 0
        miss_spec = sum(1 for p in allp if not p["spec"]) if ev["require_spec"] else 0
        ctx["checks"] = dict(miss_uid=miss_uid, miss_spec=miss_spec, no_en=sum(1 for p in allp if not p["name_en"]), n=len(allp))
    if step == 4:
        ctx["tpls"] = c.execute("SELECT id,name FROM templates WHERE active=1 ORDER BY builtin DESC, id").fetchall()
        ctx["locked"] = st["ncert"] > 0
    if step == 7:
        ctx["failed"] = c.execute("""SELECT x.id, x.serial, x.email_error, x.name_enc FROM certificates x WHERE x.event_id=? AND x.status='valid' AND x.email_status='failed'""", (eid,)).fetchall()
        ctx["failed"] = [dict(r, name=dec(r["name_enc"])) for r in ctx["failed"]]
    tpl = c.execute("SELECT name FROM templates WHERE id=?", (ev["template_id"],)).fetchone()
    ctx["tpl_name"] = tpl["name"] if tpl else "—"; ctx["reg_url"] = f"{Cfg.BASE_URL}/r/{ev['reg_token']}"
    return render_template("flow.html", **ctx)


@bp.route("/events/<int:eid>/template", methods=["POST"])
@roles("admin", "manager", "organizer")
def event_template(eid):
    ev = event_or_404(eid); c = db()
    if c.execute("SELECT 1 FROM certificates WHERE event_id=? AND status='valid'", (eid,)).fetchone():
        flash("صدرت شهادات لهذه الفعالية بالقالب الحالي؛ لا يمكن تغييره حفاظاً على الأرشيف", "err"); return _ret(eid)
    tid = int(request.form.get("template_id") or 0)
    if not c.execute("SELECT 1 FROM templates WHERE id=? AND active=1", (tid,)).fetchone(): flash("قالب غير صالح", "err"); return _ret(eid)
    c.execute("UPDATE events SET template_id=? WHERE id=?", (tid, eid)); audit("event.template", f"event:{eid}", str(tid))
    flash("تم اختيار القالب", "ok"); return _ret(eid)


@bp.route("/events/<int:eid>/preview.png")
@login_required
def event_preview(eid):
    """معاينة حيّة للشهادة بقالب الفعالية: أول مشارك معتمد إن وُجد، وإلا بيانات عامة."""
    ev = event_or_404(eid); c = db()
    tpl = c.execute("SELECT config FROM templates WHERE id=?", (ev["template_id"],)).fetchone()
    if not tpl: abort(404)
    p = c.execute("SELECT * FROM participants WHERE event_id=? AND status='approved' ORDER BY id LIMIT 1 OFFSET ?", (eid, max(0, int(request.args.get("i", 0) or 0)))).fetchone()
    from .views_admin import SAMPLE_PART
    part = cert_part(p) if p else dict(SAMPLE_PART)
    full = c.execute("SELECT e.*, d.name_ar dept_ar, d.name_en dept_en FROM events e JOIN departments d ON d.id=e.dept_id WHERE e.id=?", (eid,)).fetchone()
    with certs.Renderer() as r:
        png = r.png(certs.build_html(json.loads(tpl[0]), ev, part, "PREVIEW-0000", f"{Cfg.BASE_URL}/v/preview", services.org_for(full)), certs.page_size(json.loads(tpl[0])))
    return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})
