import json, time
from flask import Flask, g, render_template, request
from . import config, db as dbm, certs
from .security import *
from . import choices
from .util import CATEGORIES, STATUS_AR, ROLE_AR, P_STATUS, ts_fmt


def seed(c):
    if not c.execute("SELECT 1 FROM departments").fetchone():
        c.execute("INSERT INTO departments(code,name_ar,name_en) VALUES('TCGC','مركز التدريب والتوجيه الوظيفي','Training and Career Guidance Center')")
    if not c.execute("SELECT 1 FROM templates").fetchone():
        for name, cfg in certs.PRESETS:
            c.execute("INSERT INTO templates(name,config,builtin,created_at) VALUES(?,?,1,?)", (name, json.dumps(certs.clean_cfg(cfg), ensure_ascii=False), int(time.time())))


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.update(SECRET_KEY=config.subkey("session"), MAX_CONTENT_LENGTH=config.Cfg.MAX_UPLOAD + 1024 * 64,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=config.Cfg.HTTPS,
                      SESSION_COOKIE_NAME="__Host-injaz" if config.Cfg.HTTPS else "injaz", JSON_AS_ASCII=False)
    dbm.init_db()
    c = dbm.connect(); seed(c); c.close()

    app.teardown_appcontext(dbm.close_db)

    @app.before_request
    def _host_check():
        # INJAZ_ALLOWED_HOSTS: يرفض ترويسة Host غير المعروفة (حماية من Host header injection) برد بسيط دون صفحات/قوالب
        if config.Cfg.ALLOWED_HOSTS and request.host.split(":")[0].lower() not in config.Cfg.ALLOWED_HOSTS:
            from flask import Response
            return Response("Bad Request", 400, mimetype="text/plain")

    @app.before_request
    def _before():
        if request.endpoint == "static": return
        load_user(); csrf_protect()
        if g.user and g.user["role"] == "admin" and request.endpoint not in ("auth.account", "auth.logout") and not g.user["must_change_pw"]:
            from .db import setting
            if setting(dbm.db(), "require_2fa", "0") == "1" and not g.user["totp_secret"]:
                from flask import redirect, url_for, flash
                flash("سياسة الأمان تُلزم المشرفين بتفعيل التحقق بخطوتين", "warn")
                return redirect(url_for("auth.account"))

    app.after_request(secure_headers)
    app.jinja_env.globals.update(csrf_token=csrf_token, CATEGORIES=CATEGORIES, STATUS_AR=STATUS_AR, ROLE_AR=ROLE_AR, P_STATUS=P_STATUS, ts_fmt=ts_fmt, cfg=config.Cfg,
                                  choice_fields=choices.FIELDS, choice_options=choices.options, choice_label=choices.label)

    from . import views_auth, views_main, views_public, views_reports, views_admin, views_ui, views_ai, ui
    for m in (views_auth, views_main, views_public, views_reports, views_admin, views_ui, views_ai): app.register_blueprint(m.bp)
    app.context_processor(ui.context)

    def err(code, title, msg):
        return lambda e: (render_template("error.html", code=code, title=title, msg=msg), code)
    for code, t, m in ((400, "طلب غير صالح", "تعذّر تنفيذ الطلب. أعد تحميل الصفحة وحاول مجدداً."), (403, "غير مصرّح", "لا تملك صلاحية الوصول لهذه الصفحة."),
                       (404, "غير موجود", "الصفحة أو السجل غير موجود."), (413, "الملف كبير", "حجم الملف أكبر من المسموح."),
                       (429, "محاولات كثيرة", "انتظر قليلاً ثم أعد المحاولة."), (500, "خطأ داخلي", "حدث خطأ غير متوقع. تم تسجيله.")):
        app.register_error_handler(code, err(code, t, m))
    return app
