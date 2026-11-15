"""مسارات ربط حساب البريد (OAuth 2.0). الربط لحساب المستخدم الحالي فقط، ولا يعرض أي رمز."""
import hmac
from flask import Blueprint, abort, flash, g, redirect, request, url_for
from . import emailconn, mailproviders
from .db import db
from .security import audit, rate_hit, roles

bp = Blueprint("emailconn", __name__, url_prefix="/account/email")
W = ("admin", "manager", "organizer")      # من يملك صلاحية إصدار الشهادات وإرسالها


def _provider_or_404(key):
    p = mailproviders.get(key)
    if not p: abort(404)
    return p


@bp.route("/connect/<provider>")
@roles(*W)
def connect(provider):
    """رابط GET (لا نموذج) حتى لا يمنع CSP form-action التحويل لموقع المزوّد؛ محمي برمز مرتبط بالجلسة والمزوّد."""
    _provider_or_404(provider)
    if not hmac.compare_digest(request.args.get("n", ""), emailconn.connect_nonce(provider)): abort(400)
    if rate_hit(f"emailconn:{g.user['id']}", 12, 600): abort(429)
    try:
        url = emailconn.begin(db(), g.user, provider)
    except emailconn.LinkError as e:
        flash(str(e), "err"); return redirect(url_for("auth.account") + "#email")
    audit("emailconn.start", provider)
    return redirect(url)


@bp.route("/callback/<provider>")
@roles(*W)
def callback(provider):
    _provider_or_404(provider)
    try:
        flash(emailconn.finish(db(), g.user, provider, request.args), "ok")
    except emailconn.LinkError as e:
        flash(str(e), "err")
    except emailconn.ConnectionExpired:
        flash("تعذّر قراءة بيانات الربط. أعد المحاولة.", "err")
    return redirect(url_for("auth.account") + "#email")


@bp.route("/disconnect/<provider>", methods=["POST"])
@roles(*W)
def disconnect(provider):
    _provider_or_404(provider)
    if emailconn.disconnect(db(), g.user, provider): flash("تم فصل الحساب وحذف رموز الوصول المخزّنة. يمكنك أيضاً إزالة تفويض التطبيق من إعدادات حساب المزوّد.", "ok")
    else: flash("لا يوجد حساب مرتبط", "warn")
    return redirect(url_for("auth.account") + "#email")
