import datetime, secrets, time
from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from . import services
from .config import Cfg
from .db import db, setting
from .security import *
from .util import *

bp = Blueprint("public", __name__)
OMAN = datetime.timezone(datetime.timedelta(hours=4))


def _now_local():
    return datetime.datetime.now(OMAN).strftime("%Y-%m-%dT%H:%M")


def _reg_state(ev):
    if not ev["reg_open"]: return "closed"
    n = _now_local()
    if ev["reg_opens_at"] and n < ev["reg_opens_at"]: return "notyet"
    if ev["reg_closes_at"] and n > ev["reg_closes_at"]: return "closed"
    if ev["max_participants"]:
        if db().execute("SELECT COUNT(*) FROM participants WHERE event_id=? AND status!='rejected'", (ev["id"],)).fetchone()[0] >= ev["max_participants"]: return "full"
    return "open"


@bp.route("/r/<token>", methods=["GET", "POST"])
def register(token):
    c = db()
    ev = c.execute("SELECT e.*, d.name_ar dept_ar, d.name_en dept_en FROM events e JOIN departments d ON d.id=e.dept_id WHERE reg_token=? AND e.deleted_at IS NULL", (token,)).fetchone()
    if not ev: abort(404)
    state = _reg_state(ev)
    if state != "open": return render_template("reg_closed.html", ev=ev, state=state)
    if request.method == "POST":
        f = request.form
        if rate_hit("reg:ip:" + client_ip(), 15, 3600) or rate_hit(f"reg:ev:{ev['id']}", 1500, 3600):
            return render_template("reg_closed.html", ev=ev, state="rate"), 429
        t0 = session.get("reg_t:" + token, 0)
        if f.get("website") or time.time() - t0 < Cfg.REG_MIN_SECONDS:  # مصيدة الروبوتات + زمن تعبئة منطقي
            audit("reg.bot_blocked", f"event:{ev['id']}")
            flash("تعذّر إرسال النموذج، أعد المحاولة", "err"); return redirect(request.url)
        if ev["reg_pin"] and not hmac.compare_digest(f.get("pin", "").strip(), ev["reg_pin"]):
            flash("رمز الحضور غير صحيح (اطلبه من المنظم)", "err"); return render_template("register.html", ev=ev, v=f), 400
        good, bad = services.validate_rows([form_row(f)], ev,
                                           {r[0] for r in c.execute("SELECT dedupe FROM participants WHERE event_id=?", (ev["id"],))}, strict=True)
        if bad:
            msg = "، ".join(bad[0]["errors"])
            if "مكرر" in msg: return render_template("reg_done.html", ev=ev, dup=True)
            flash(msg, "err"); return render_template("register.html", ev=ev, v=f), 400
        from .views_main import _add_participant
        r = good[0]
        _add_participant(c, ev, r["name"], r["name_en"], r["uid"], r["email"], r["spec"], "qr", "approved" if ev["auto_approve"] else "pending", r["acad"], r["gender"], r["nat"])
        audit("reg.ok", f"event:{ev['id']}", conn=c)
        session.pop("reg_t:" + token, None)
        return render_template("reg_done.html", ev=ev, dup=False)
    session["reg_t:" + token] = time.time()
    return render_template("register.html", ev=ev, v={})


@bp.route("/verify", methods=["GET", "POST"])
def verify_form():
    if request.method == "POST":
        code = re.sub(r"[^A-Za-z0-9]", "", request.form.get("code", "")).upper()
        if len(code) == 12: return redirect(url_for("public.verify", code=f"{code[:4]}-{code[4:8]}-{code[8:]}"))
        flash("رمز التحقق مكوّن من 12 خانة مثل K7QM-4TXD-9HPA", "err")
    return render_template("verify_form.html")


import re, hmac

@bp.route("/v/<code>")
def verify(code):
    if rate_hit("verify:" + client_ip(), 60, 300): abort(429)
    code = code.upper()[:20]
    x = db().execute("""SELECT x.*, e.title_ar, e.title_en, e.event_date, e.end_date, e.hours, d.name_ar dept_ar, d.name_en dept_en
        FROM certificates x JOIN events e ON e.id=x.event_id JOIN departments d ON d.id=e.dept_id WHERE x.verify_code=? AND e.deleted_at IS NULL""", (code,)).fetchone()
    mode = setting(db(), "verify_name_mode", "masked")
    name = ""
    if x:
        n = dec(x["name_enc"]); name = n if mode == "full" else mask_name(n) if mode == "masked" else ""
    return render_template("verify.html", x=x, name=name, code=code), (200 if x else 404)


@bp.route("/get/<token>")
def get_cert(token):
    if rate_hit("get:" + client_ip(), 60, 300): abort(429)
    x = db().execute("SELECT x.* FROM certificates x JOIN events e ON e.id=x.event_id WHERE x.dl_hash=? AND x.dl_expires>? AND e.deleted_at IS NULL", (token_hash(token), int(time.time()))).fetchone()
    if not x or x["status"] != "valid": return render_template("get_invalid.html"), 404
    db().execute("UPDATE certificates SET downloads=downloads+1 WHERE id=?", (x["id"],))
    return Response(services.read_cert_pdf(x), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={x['serial']}.pdf"})


@bp.route("/my", methods=["GET", "POST"])
def portal():
    sent = False
    if request.method == "POST":
        if rate_hit("portal:ip:" + client_ip(), 8, 3600): abort(429)
        v = clean_text(request.form.get("q"), 120)
        ui, ei = blind("u", v), blind("e", v.lower())
        rows = db().execute("SELECT DISTINCT email_enc FROM participants WHERE event_id IN (SELECT id FROM events WHERE deleted_at IS NULL) AND ((uid_idx=? AND uid_idx!='') OR (email_idx=? AND email_idx!=''))", (ui, ei)).fetchall()
        emails = {dec(r[0]) for r in rows if r[0]}
        emails = {e for e in emails if EMAIL_RE.match(e)}
        if emails and not rate_hit("portal:q:" + (ui or ei), 3, 3600):
            t = secrets.token_urlsafe(32)
            db().execute("INSERT INTO portal_tokens(token_hash,uid_idx,email_idx,expires) VALUES(?,?,?,?)", (token_hash(t), ui, ei, int(time.time()) + 3600))
            body = f"رابط الاطلاع على شهاداتك (صالح لمدة ساعة):\n{Cfg.BASE_URL}/my/{t}\n\nYour certificates link (valid for 1 hour):\n{Cfg.BASE_URL}/my/{t}"
            for e in emails:
                try: services.send_plain(e, "شهاداتك | Your certificates", body, "portal")
                except Exception: current_app.logger.warning("portal mail failed")
        sent = True  # نفس الرد دائماً: لا نكشف وجود الحسابات
    return render_template("portal.html", sent=sent)


def _portal_certs(token):
    pt = db().execute("SELECT * FROM portal_tokens WHERE token_hash=? AND expires>?", (token_hash(token), int(time.time()))).fetchone()
    if not pt: abort(404)
    return db().execute("""SELECT x.*, e.title_ar, e.event_date, e.hours FROM certificates x JOIN participants p ON p.id=x.participant_id JOIN events e ON e.id=x.event_id
        WHERE x.status='valid' AND e.deleted_at IS NULL AND ((p.uid_idx=? AND p.uid_idx!='') OR (p.email_idx=? AND p.email_idx!='')) ORDER BY e.event_date DESC""", (pt["uid_idx"], pt["email_idx"])).fetchall()


@bp.route("/my/<token>")
def portal_list(token):
    return render_template("portal_list.html", rows=_portal_certs(token), token=token)


@bp.route("/my/<token>/<serial>.pdf")
def portal_pdf(token, serial):
    for x in _portal_certs(token):
        if x["serial"] == serial:
            db().execute("UPDATE certificates SET downloads=downloads+1 WHERE id=?", (x["id"],))
            return Response(services.read_cert_pdf(x), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={serial}.pdf"})
    abort(404)
