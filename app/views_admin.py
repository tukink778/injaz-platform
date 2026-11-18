import hashlib, json, re, secrets, time
from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from . import certs
from .db import db, setting, set_setting
from .security import *
from .util import *
from . import services

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------- المستخدمون ----------
def _credentials(username, password, full_name, role, kind):
    """بيانات الدخول المؤقتة تُعرض مرة واحدة في بطاقة (لا تُخزَّن ولا تُسجَّل) مع رسالة جاهزة للإرسال. المستخدم يُجبر على تغيير كلمة المرور عند أول دخول."""
    from .config import Cfg
    url = Cfg.BASE_URL + "/login"
    head = "تم إنشاء حسابك" if kind == "create" else "تمت إعادة تعيين كلمة مرور حسابك"
    msg = (f"السلام عليكم {full_name}،\n{head} في منصة إنجاز (الدور: {ROLE_AR.get(role, role)}).\n\n"
           f"رابط الدخول: {url}\nاسم المستخدم: {username}\nكلمة المرور المؤقتة: {password}\n\n"
           "سيُطلب منك تغيير كلمة المرور عند أول تسجيل دخول. يرجى عدم مشاركة هذه البيانات مع أحد.")
    return dict(username=username, password=password, full_name=full_name, role_ar=ROLE_AR.get(role, role), url=url, message=msg, kind=kind)

@bp.route("/users", methods=["GET", "POST"])
@roles("admin")
def users():
    c = db(); newpw = None; cred = None
    if request.method == "POST":
        f = request.form; act = f.get("act")
        if act == "create":
            un = re.sub(r"[^a-z0-9._\-]", "", f.get("username", "").lower())[:40]
            role = f.get("role"); dept = int(f.get("dept_id") or 0) or None
            if not un or role not in ROLE_AR or (role != "admin" and not dept) or c.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
                flash("بيانات غير صالحة أو اسم المستخدم مستخدم. غير المشرف العام يحتاج قسماً", "err")
            else:
                newpw = secrets.token_urlsafe(12) + "aA1"
                c.execute("INSERT INTO users(username,full_name,email,pw_hash,role,dept_id,must_change_pw,created_at) VALUES(?,?,?,?,?,?,1,?)",
                          (un, clean_text(f.get("full_name"), 80) or un, clean_text(f.get("email"), 120), hash_pw(newpw), role, dept, int(time.time())))
                audit("user.create", un, role); flash(f"أُنشئ المستخدم «{un}». بيانات الدخول تظهر مرة واحدة في البطاقة أدناه", "ok")
                cred = _credentials(un, newpw, clean_text(f.get("full_name"), 80) or un, role, "create")
        else:
            u = c.execute("SELECT * FROM users WHERE id=?", (int(f.get("uid") or 0),)).fetchone()
            if not u: abort(404)
            if act == "reset":
                pw = secrets.token_urlsafe(12) + "aA1"
                c.execute("UPDATE users SET pw_hash=?, must_change_pw=1, session_ver=session_ver+1, failed=0, locked_until=0 WHERE id=?", (hash_pw(pw), u["id"]))
                audit("user.reset", u["username"]); flash("أُعيد تعيين كلمة المرور. بيانات الدخول الجديدة في البطاقة أدناه", "ok")
                cred = _credentials(u["username"], pw, u["full_name"], u["role"], "reset")
            elif act == "toggle" and u["id"] != g.user["id"]:
                c.execute("UPDATE users SET active=1-active, session_ver=session_ver+1 WHERE id=?", (u["id"],)); audit("user.toggle", u["username"])
            elif act == "unlock":
                c.execute("UPDATE users SET failed=0, locked_until=0 WHERE id=?", (u["id"],)); audit("user.unlock", u["username"])
            elif act == "reset2fa":
                c.execute("UPDATE users SET totp_secret=NULL WHERE id=?", (u["id"],)); audit("user.reset2fa", u["username"])
            elif act == "role" and u["id"] != g.user["id"] and f.get("role") in ROLE_AR:
                dept = int(f.get("dept_id") or 0) or None
                c.execute("UPDATE users SET role=?, dept_id=?, session_ver=session_ver+1 WHERE id=?", (f["role"], dept, u["id"])); audit("user.role", u["username"], f["role"])
    us = c.execute("SELECT u.*, d.name_ar dept_ar FROM users u LEFT JOIN departments d ON d.id=u.dept_id ORDER BY u.id").fetchall()
    depts = c.execute("SELECT * FROM departments WHERE active=1 ORDER BY name_ar").fetchall()
    return render_template("admin_users.html", us=us, depts=depts, roles=ROLE_AR, cred=cred)


# ---------- الأقسام ----------
@bp.route("/departments", methods=["GET", "POST"])
@roles("admin")
def departments():
    c = db()
    if request.method == "POST":
        f = request.form
        if f.get("act") == "create":
            code = re.sub(r"[^A-Z0-9]", "", f.get("code", "").upper())[:8]
            if not code or not f.get("name_ar") or c.execute("SELECT 1 FROM departments WHERE code=?", (code,)).fetchone():
                flash("الرمز مطلوب وفريد (حروف إنجليزية/أرقام) والاسم العربي مطلوب", "err")
            else:
                c.execute("INSERT INTO departments(code,name_ar,name_en) VALUES(?,?,?)", (code, clean_text(f["name_ar"], 100), clean_text(f.get("name_en"), 100)))
                audit("dept.create", code); flash("أُضيف القسم", "ok")
        elif f.get("act") == "toggle":
            c.execute("UPDATE departments SET active=1-active WHERE id=?", (int(f.get("id") or 0),)); audit("dept.toggle", f.get("id"))
        elif f.get("act") == "edit":
            c.execute("UPDATE departments SET name_ar=?, name_en=? WHERE id=?", (clean_text(f.get("name_ar"), 100), clean_text(f.get("name_en"), 100), int(f.get("id") or 0)))
            audit("dept.edit", f.get("id")); flash("تم الحفظ", "ok")
    rows = c.execute("SELECT d.*, (SELECT COUNT(*) FROM events WHERE dept_id=d.id) ne FROM departments d ORDER BY d.id").fetchall()
    return render_template("admin_depts.html", rows=rows)


# ---------- القوالب ----------
# بيانات عرض عامة بلا أي معلومات شخصية (للمعاينة فقط)
SAMPLE_EV = dict(title_ar="عنوان الفعالية", title_en="Event Title", event_date="2026-01-01", end_date="2026-01-05", hours=10, location="",
                 signatory_name="اسم الموقّع", signatory_title_en="Signatory Title", signatory_title_ar="صفة الموقّع")
SAMPLE_PART = dict(name="اسم المشارك", name_en="Participant Name", uid="00000000", spec="اسم التخصص", acad="student", gender="male", nat="omani")
_prev_cache = {}


def _preview_png(cfg):
    key = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    if key not in _prev_cache:
        if len(_prev_cache) > 40: _prev_cache.clear()
        with certs.Renderer() as r:
            _prev_cache[key] = r.png(certs.build_html(cfg, SAMPLE_EV, SAMPLE_PART, "SAMPLE-2026-00001", "https://example.org/v/SAMPLE", certs.org_for_sample()), certs.page_size(cfg))
    return _prev_cache[key]


def _tpl_or_404(tid):
    t = db().execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
    if not t: abort(404)
    return t


def _usage(tid):
    c = db()
    ne = c.execute("SELECT COUNT(*) FROM events WHERE template_id=?", (tid,)).fetchone()[0]
    nc = c.execute("SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE e.template_id=?", (tid,)).fetchone()[0]
    return ne, nc


@bp.route("/templates")
@roles("admin")
def templates():
    rows = db().execute("""SELECT t.*, (SELECT COUNT(*) FROM events WHERE template_id=t.id) ne,
        (SELECT COUNT(*) FROM certificates x JOIN events e ON e.id=x.event_id WHERE e.template_id=t.id) nc FROM templates t ORDER BY t.active DESC, t.builtin DESC, t.id""").fetchall()
    rows = [dict(r, layout=json.loads(r["config"]).get("layout"), orient=json.loads(r["config"]).get("orientation", "landscape")) for r in rows]
    return render_template("admin_templates.html", rows=rows, presets=[(i, n) for i, (n, _) in enumerate(certs.PRESETS)])


@bp.route("/templates/<int:tid>/preview.png")
@roles("admin", "manager", "organizer")
def template_preview(tid):
    t = _tpl_or_404(tid); cfg = json.loads(t["config"])
    return Response(_preview_png(cfg), mimetype="image/png", headers={"Cache-Control": "private, max-age=300"})


def _cfg_from_form(f, old=None):
    cfg = dict(old or {})
    for k in ("layout", "title_ar", "title_en", "intro_ar", "intro_en", "body_ar", "body_en", "closing_ar", "closing_en", "primary", "accent", "background"):
        if k in f: cfg[k] = f[k]
    for k in ("show_uid", "show_spec", "show_stamp", "show_signature", "show_qr"): cfg[k] = bool(f.get(k))
    return certs.clean_cfg(cfg)


def _json_cfg():
    try: raw = json.loads(request.form.get("cfg", "{}"))
    except ValueError: abort(400)
    if not isinstance(raw, dict): abort(400)
    raw["layout"] = "custom"
    return certs.clean_cfg(raw)


@bp.route("/templates/live", methods=["POST"])
@roles("admin")
def template_live():
    if request.form.get("cfg"): cfg = _json_cfg()
    else:
        cfg = _cfg_from_form(request.form.to_dict(), {})
        if request.form.get("background") not in ("official", "plain"): cfg["background"] = request.form.get("background")
        cfg = certs.clean_cfg(cfg)
    return Response(_preview_png(cfg), mimetype="image/png")


@bp.route("/templates/new", methods=["POST"])
@roles("admin")
def template_new():
    mode = request.form.get("mode", "preset")
    if mode == "blank":
        orient = "portrait" if request.form.get("orientation") == "portrait" else "landscape"
        cfg = certs.clean_cfg(dict(layout="custom", orientation=orient, background="plain", elements=[]))
        name = "قالب جديد (" + ("عمودي" if orient == "portrait" else "أفقي") + ")"
    else:
        i = int(request.form.get("preset", 0) or 0)
        name, cfg = certs.PRESETS[i % len(certs.PRESETS)]
        cfg = certs.clean_cfg(cfg); name += " (نسخة)"
    tid = db().insert("INSERT INTO templates(name,config,created_at) VALUES(?,?,?)", (name, json.dumps(cfg, ensure_ascii=False), int(time.time()))); audit("template.create", str(tid))
    flash("أُنشئ القالب. عدّله ثم فعّله ليظهر عند إنشاء الفعاليات", "ok")
    return redirect(url_for("admin.template_edit", tid=tid))


@bp.route("/templates/<int:tid>/duplicate", methods=["POST"])
@roles("admin")
def template_duplicate(tid):
    t = _tpl_or_404(tid)
    nid = db().insert("INSERT INTO templates(name,config,builtin,active,created_at) VALUES(?,?,0,1,?)", (t["name"] + " (نسخة)", t["config"], int(time.time()))); audit("template.duplicate", str(tid), f"->{nid}")
    flash("تم نسخ القالب", "ok"); return redirect(url_for("admin.template_edit", tid=nid))


@bp.route("/templates/<int:tid>/toggle", methods=["POST"])
@roles("admin")
def template_toggle(tid):
    t = _tpl_or_404(tid); c = db()
    if t["active"] and c.execute("SELECT COUNT(*) FROM templates WHERE active=1").fetchone()[0] <= 1:
        flash("يجب أن يبقى قالب واحد مفعّل على الأقل", "err"); return redirect(url_for("admin.templates"))
    c.execute("UPDATE templates SET active=1-active WHERE id=?", (tid,)); audit("template.toggle", str(tid))
    flash("تم تعطيل القالب (لن يظهر في الفعاليات الجديدة، والفعاليات القائمة لا تتأثر)" if t["active"] else "تم تفعيل القالب", "ok")
    return redirect(request.referrer or url_for("admin.templates"))


@bp.route("/templates/<int:tid>/delete", methods=["GET", "POST"])
@roles("admin")
def template_delete(tid):
    t = _tpl_or_404(tid); c = db(); ne, nc = _usage(tid)
    others = c.execute("SELECT id,name FROM templates WHERE id!=? AND active=1 ORDER BY name", (tid,)).fetchall()
    if request.method == "POST":
        target = int(request.form.get("reassign") or 0)
        if ne:
            if not any(o["id"] == target for o in others):
                flash("اختر قالباً مفعّلاً لنقل الفعاليات إليه قبل الحذف", "err"); return redirect(url_for("admin.template_delete", tid=tid))
        elif not others and t["active"]:
            flash("لا يمكن حذف آخر قالب مفعّل", "err"); return redirect(url_for("admin.templates"))
        if request.form.get("confirm") != "yes": abort(400)
        if ne: c.execute("UPDATE events SET template_id=? WHERE template_id=?", (target, tid))
        c.execute("DELETE FROM templates WHERE id=?", (tid,)); audit("template.delete", str(tid), f"events={ne} reassign={target or '-'}")
        flash("حُذف القالب. الشهادات الصادرة سابقاً (ملفات PDF) لم تتغير" + (f" ونُقلت {ne} فعالية للقالب البديل" if ne else ""), "ok")
        return redirect(url_for("admin.templates"))
    return render_template("admin_template_delete.html", t=t, ne=ne, nc=nc, others=others)


@bp.route("/templates/asset", methods=["POST"])
@roles("admin")
def template_asset():
    f = request.files.get("file")
    if not f or not f.filename: return jsonify(error="لم يُرفع ملف"), 400
    try:
        name = certs.save_background(f.stream) if request.form.get("kind") == "bg" else certs.save_asset(f.stream)
    except ValueError as e: return jsonify(error=str(e)), 400
    except Exception: return jsonify(error="ملف صورة غير صالح"), 400
    audit("template.asset", name); return jsonify(name=name)


@bp.route("/assets/<name>")
@roles("admin")
def template_asset_get(name):
    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg)", name): abort(404)
    pth = certs.ASSETS / name
    if not pth.exists(): abort(404)
    return send_file(pth, mimetype="image/png" if name.endswith(".png") else "image/jpeg", max_age=3600)


@bp.route("/templates/<int:tid>", methods=["GET", "POST"])
@roles("admin")
def template_edit(tid):
    c = db(); t = _tpl_or_404(tid)
    cfg = json.loads(t["config"]); ne, nc = _usage(tid)
    if request.method == "POST":
        f = request.form.to_dict(); act = f.get("act")
        if cfg.get("layout") == "custom" and f.get("cfg"):
            cfg = _json_cfg()
        elif cfg.get("layout") != "custom":
            cfg = _cfg_from_form(f, cfg)
            up = request.files.get("bg_file")
            if up and up.filename:
                try: cfg["background"] = certs.save_background(up.stream)
                except Exception as e: flash(f"تعذّر قبول الصورة: {e if isinstance(e, ValueError) else 'ملف غير صالح'}", "err")
        c.execute("UPDATE templates SET name=?, config=? WHERE id=?", (clean_text(f.get("name"), 80) or t["name"], json.dumps(cfg, ensure_ascii=False), tid))
        _prev_cache.clear(); audit("template.edit", str(tid))
        if request.headers.get("X-Requested-With") == "fetch": return jsonify(ok=True)
        flash("تم حفظ القالب. الشهادات التي صدرت سابقاً لا تتغير", "ok")
        return redirect(url_for("admin.template_edit", tid=tid))
    if cfg.get("layout") == "custom":
        return render_template("admin_template_visual.html", t=t, cfg=cfg, ne=ne, nc=nc, placeholders=certs.PLACEHOLDERS)
    return render_template("admin_template_edit.html", t=t, cfg=cfg, ne=ne, nc=nc, ver=hashlib.md5(t["config"].encode(), usedforsecurity=False).hexdigest()[:8])


# ---------- الإعدادات ----------
@bp.route("/settings", methods=["GET", "POST"])
@roles("admin")
def settings():
    c = db()
    keys = ["email_subject", "email_body", "attach_pdf", "token_days", "survey_url", "verify_name_mode", "require_2fa", "ai_enabled", "trash_days"]
    if request.method == "POST":
        f = request.form
        set_setting(c, "email_subject", clean_text(f.get("email_subject"), 200) or services.DEFAULT_SUBJECT)
        set_setting(c, "email_body", (f.get("email_body") or services.DEFAULT_BODY)[:3000])
        set_setting(c, "attach_pdf", "1" if f.get("attach_pdf") else "0")
        set_setting(c, "token_days", str(max(1, min(730, int(f.get("token_days") or 90)))))
        sv = clean_text(f.get("survey_url"), 300)
        set_setting(c, "survey_url", sv if sv.startswith("https://") or not sv else "")
        set_setting(c, "verify_name_mode", f.get("verify_name_mode") if f.get("verify_name_mode") in ("masked", "full", "hidden") else "masked")
        set_setting(c, "trash_days", str(max(1, min(365, int(f.get("trash_days") or services.trash_days(c))))))
        set_setting(c, "require_2fa", "1" if f.get("require_2fa") else "0")
        set_setting(c, "ai_enabled", "1" if f.get("ai_enabled") else "0")
        audit("settings.update"); flash("تم حفظ الإعدادات", "ok")
        return redirect(url_for("admin.settings"))
    s = {k: setting(c, k, "") for k in keys}
    s["email_subject"] = s["email_subject"] or services.DEFAULT_SUBJECT; s["email_body"] = s["email_body"] or services.DEFAULT_BODY
    s["trash_days"] = s["trash_days"] or str(services.trash_days(c)); s["token_days"] = s["token_days"] or "90"; s["ai_enabled"] = s["ai_enabled"] or "1"; s["verify_name_mode"] = s["verify_name_mode"] or "masked"
    from .config import Cfg
    return render_template("admin_settings.html", s=s, Cfg=Cfg)


# ---------- التدقيق ----------
@bp.route("/audit")
@roles("admin")
def audit_log():
    c = db(); q = clean_text(request.args.get("q"), 60)
    sql, a = "SELECT * FROM audit", []
    if q: sql += " WHERE action LIKE ? OR username LIKE ? OR target LIKE ?"; a = [f"%{q}%"] * 3
    rows = c.execute(sql + " ORDER BY id DESC LIMIT 300", a).fetchall()
    ok, bad = audit_verify(c) if request.args.get("verify") else (None, None)
    return render_template("admin_audit.html", rows=rows, q=q, ok=ok, bad=bad)


# ---------- حسابات البريد المرتبطة (OAuth) — إحصاءات بلا أي رموز ----------
@bp.route("/email-accounts")
@roles("admin")
def email_accounts():
    from . import emailconn
    from .config import Cfg
    return render_template("admin_email_accounts.html", S=emailconn.admin_stats(db()), smtp_ok=services.mail_configured(), mail_mode=Cfg.MAIL_MODE)


@bp.route("/email-accounts/<int:cid>/remove", methods=["POST"])
@roles("admin")
def email_account_remove(cid):
    from . import emailconn
    flash("فُصل الحساب وحُذفت رموزه" if emailconn.remove_by_admin(db(), g.user, cid) else "غير موجود", "ok")
    return redirect(url_for("admin.email_accounts"))
