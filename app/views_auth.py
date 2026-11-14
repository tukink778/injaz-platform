import time
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, g
from .db import db
from .security import *
from .security import _DUMMY

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user: return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        un = (request.form.get("username") or "").strip().lower()[:64]
        if rate_hit("login:ip:" + client_ip(), 20, 600) or rate_hit("login:u:" + un, 10, 900):
            audit("login.ratelimited", un)
            flash("محاولات كثيرة. انتظر بضع دقائق ثم أعد المحاولة.", "err")
            return render_template("login.html"), 429
        u = db().execute("SELECT * FROM users WHERE username=?", (un,)).fetchone()
        ok = check_pw(u["pw_hash"] if u else None, request.form.get("password") or "")
        now = int(time.time())
        if u and u["locked_until"] > now:
            ok = False
        if not (u and ok and u["active"]):
            if u:
                f = u["failed"] + 1
                lock = now + 900 if f >= 5 else 0
                db().execute("UPDATE users SET failed=?, locked_until=? WHERE id=?", (0 if lock else f, lock, u["id"]))
            audit("login.fail", un)
            flash("بيانات الدخول غير صحيحة أو الحساب مقفل مؤقتاً", "err")
            return render_template("login.html"), 401
        db().execute("UPDATE users SET failed=0 WHERE id=?", (u["id"],))
        if u["totp_secret"]:
            session.clear(); session["pre2fa"] = u["id"]; session["pre2fa_t"] = now
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect(url_for("auth.login_2fa"))
        return _finish(u)
    return render_template("login.html")


def _finish(u):
    nxt = request.args.get("next", "")
    login_user(u)
    db().execute("UPDATE users SET last_login=? WHERE id=?", (int(time.time()), u["id"]))
    audit("login.ok", u["username"], user=u)
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))


@bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    uid = session.get("pre2fa")
    if not uid or time.time() - session.get("pre2fa_t", 0) > 300:
        return redirect(url_for("auth.login"))
    u = db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    if request.method == "POST":
        if rate_hit(f"2fa:{uid}", 6, 600):
            flash("محاولات كثيرة، أعد تسجيل الدخول لاحقاً", "err"); return redirect(url_for("auth.login"))
        if u and totp_ok(u["totp_secret"], request.form.get("code", "")):
            return _finish(u)
        audit("login.2fa_fail", u["username"] if u else "", user=u)
        flash("الرمز غير صحيح", "err")
    return render_template("login_2fa.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user: audit("logout")
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    u = g.user
    if request.method == "POST":
        act = request.form.get("act")
        if act == "pw":
            if not check_pw(u["pw_hash"], request.form.get("old", "")):
                flash("كلمة المرور الحالية غير صحيحة", "err")
            elif request.form.get("new") != request.form.get("new2"):
                flash("كلمتا المرور غير متطابقتين", "err")
            elif (p := pw_problem(request.form.get("new", ""))):
                flash(p, "err")
            elif check_pw(u["pw_hash"], request.form.get("new", "")):
                flash("اختر كلمة مرور مختلفة عن الحالية", "err")
            else:
                db().execute("UPDATE users SET pw_hash=?, must_change_pw=0, session_ver=session_ver+1 WHERE id=?", (hash_pw(request.form["new"]), u["id"]))
                audit("account.pw_change", u["username"])
                nu = db().execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
                login_user(nu); flash("تم تغيير كلمة المرور", "ok")
                return redirect(url_for("auth.account"))
        elif act == "2fa_start":
            s = new_totp()
            session["totp_pending"] = enc(s)
        elif act == "2fa_confirm":
            pend = session.get("totp_pending")
            if pend and totp_ok(pend, request.form.get("code", "")):
                db().execute("UPDATE users SET totp_secret=? WHERE id=?", (pend, u["id"]))
                session.pop("totp_pending", None); audit("account.2fa_on", u["username"]); flash("تم تفعيل التحقق بخطوتين", "ok")
            else:
                flash("الرمز غير صحيح", "err")
        elif act == "2fa_off":
            if u["role"] == "admin" and db().execute("SELECT value FROM settings WHERE key='require_2fa'").fetchone() and \
               db().execute("SELECT value FROM settings WHERE key='require_2fa'").fetchone()[0] == "1":
                flash("سياسة النظام تُلزم المشرفين بالتحقق بخطوتين", "err")
            elif check_pw(u["pw_hash"], request.form.get("old", "")):
                db().execute("UPDATE users SET totp_secret=NULL WHERE id=?", (u["id"],)); audit("account.2fa_off", u["username"]); flash("تم إيقاف التحقق بخطوتين", "ok")
            else:
                flash("كلمة المرور غير صحيحة", "err")
        return redirect(url_for("auth.account"))
    pend = session.get("totp_pending")
    qr = totp_qr_uri(pend, u["username"]) if pend else None
    secret = dec(pend) if pend else None
    return render_template("account.html", qr=qr, secret=secret)
