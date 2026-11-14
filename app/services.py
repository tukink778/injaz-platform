"""خدمات خلفية: الإصدار، البريد، استيراد Excel، المهام الطويلة."""
import io, json, secrets, smtplib, ssl, threading, time, zipfile, re
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from . import certs, choices
from .config import Cfg, DATA
from .db import connect, setting, next_counter
from .security import audit, blind, dec, enc_bytes, dec_bytes, token_hash
from .util import cert_part, EMAIL_RE, UID_RE, clean_text, valid_name, new_verify_code

# ================= المهام الخلفية =================

def start_job(app, kind, event_id, user, total, fn):
    c = connect()
    c.execute("INSERT INTO jobs(kind,event_id,user_id,total,created_at) VALUES(?,?,?,?,?)", (kind, event_id, user["id"], total, int(time.time())))
    jid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.close()
    u = dict(user)

    def run():
        c = connect()
        try:
            res = fn(c, jid, u)
            c.execute("UPDATE jobs SET status='done', message=? WHERE id=?", (res or "", jid))
        except Exception as e:  # noqa
            app.logger.exception("job failed")
            c.execute("UPDATE jobs SET status='error', message=? WHERE id=?", (f"خطأ: {type(e).__name__}", jid))
        finally:
            c.close()
    threading.Thread(target=run, daemon=True).start()
    return jid


def _tick(c, jid, ok=True):
    c.execute("UPDATE jobs SET done=done+1, failed=failed+?  WHERE id=?", (0 if ok else 1, jid))


# ================= الإصدار =================

def org_for(ev):
    return dict(dept_ar=ev["dept_ar"], dept_en=ev["dept_en"] or ev["dept_ar"], org_en="University of Technology and Applied Sciences, Suhar")


def cert_path(serial):
    return DATA / "certs" / (re.sub(r"[^A-Za-z0-9\-]", "", serial) + ".pdf.enc")


def issue_certs(c, jid, user, event_id, pids):
    ev = c.execute("SELECT e.*, d.name_ar dept_ar, d.name_en dept_en, d.code dept_code FROM events e JOIN departments d ON d.id=e.dept_id WHERE e.id=?", (event_id,)).fetchone()
    tpl = json.loads(c.execute("SELECT config FROM templates WHERE id=?", (ev["template_id"],)).fetchone()[0])
    year = ev["event_date"][:4]
    done = 0
    with certs.Renderer() as r:
        for pid in pids:
            p = c.execute("SELECT * FROM participants WHERE id=? AND event_id=? AND status='approved'", (pid, event_id)).fetchone()
            if not p or c.execute("SELECT 1 FROM certificates WHERE participant_id=? AND status='valid'", (pid,)).fetchone():
                _tick(c, jid); continue
            try:
                c.execute("BEGIN IMMEDIATE")
                n = next_counter(c, f"serial:{ev['dept_code']}:{year}")
                serial = f"{ev['dept_code']}-{year}-{n:05d}"
                code = new_verify_code()
                c.execute("COMMIT")
                url = f"{Cfg.BASE_URL}/v/{code}"
                part = cert_part(p)
                pdf = r.pdf(certs.build_html(tpl, ev, part, serial, url, org_for(ev)), certs.page_size(tpl))
                cert_path(serial).write_bytes(enc_bytes(pdf))
                c.execute("INSERT INTO certificates(participant_id,event_id,serial,verify_code,name_enc,issued_at,issued_by,file) VALUES(?,?,?,?,?,?,?,?)",
                          (pid, event_id, serial, code, p["name_enc"], int(time.time()), user["id"], cert_path(serial).name))
                _tick(c, jid)
            except Exception:
                _tick(c, jid, False)
    left = c.execute("SELECT COUNT(*) FROM participants p WHERE event_id=? AND status='approved' AND NOT EXISTS(SELECT 1 FROM certificates x WHERE x.participant_id=p.id AND x.status='valid')", (event_id,)).fetchone()[0]
    if left == 0:
        c.execute("UPDATE events SET status='issued' WHERE id=?", (event_id,))
    n = c.execute("SELECT done-failed ok, failed FROM jobs WHERE id=?", (jid,)).fetchone()
    audit("certs.issue", f"event:{event_id}", f"ok={n['ok']} failed={n['failed']}", conn=c, user=user)
    return f"صدرت {n['ok']} شهادة" + (f"، وفشلت {n['failed']}" if n["failed"] else "")


def read_cert_pdf(cert):
    return dec_bytes(cert_path(cert["serial"]).read_bytes())


# ================= البريد =================

def mail_configured():
    return Cfg.MAIL_MODE in ("smtp", "file")


def new_download_token(c, cert_id, days=None):
    days = days or int(setting(c, "token_days", "90"))
    t = secrets.token_urlsafe(32)
    c.execute("UPDATE certificates SET dl_hash=?, dl_expires=? WHERE id=?", (token_hash(t), int(time.time()) + days * 86400, cert_id))
    return t


DEFAULT_SUBJECT = "شهادتك من {dept} | Your certificate"
DEFAULT_BODY = ("عزيزي/عزيزتي {name}،\nيسعدنا إبلاغك بصدور شهادتك: {event}.\nيمكنك تحميل الشهادة من الرابط الآمن التالي:\n{link}\n\n"
                "رقم الشهادة: {serial}\nرمز التحقق: {code}\n\n— — —\nDear {name},\nYour certificate for \"{event}\" is ready. Download it securely here:\n{link}")


def send_cert_email(c, cert, to_addr, name, ev, attach):
    link_tok = new_download_token(c, cert["id"])
    link = f"{Cfg.BASE_URL}/get/{link_tok}"
    vals = dict(name=name, event=ev["title_ar"], dept=ev["dept_ar"], link=link, serial=cert["serial"], code=cert["verify_code"])
    subj = setting(c, "email_subject", DEFAULT_SUBJECT)
    body = setting(c, "email_body", DEFAULT_BODY)
    for k, v in vals.items():
        subj = subj.replace("{" + k + "}", v); body = body.replace("{" + k + "}", v)
    survey = setting(c, "survey_url", "")
    if survey: body += f"\n\nرأيك يهمنا / Feedback: {survey}"
    msg = EmailMessage()
    msg["Subject"] = subj.replace("\n", " ")[:200]; msg["From"] = Cfg.SMTP_FROM; msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True); msg["Message-ID"] = make_msgid(domain="injaz.local")
    msg.set_content(body)
    if attach:
        msg.add_attachment(read_cert_pdf(cert), maintype="application", subtype="pdf", filename=f"{cert['serial']}.pdf")
    if Cfg.MAIL_MODE == "file":
        (DATA / "outbox" / f"{cert['serial']}.eml").write_bytes(bytes(msg))
        return
    if Cfg.SMTP_SECURITY == "ssl":
        s = smtplib.SMTP_SSL(Cfg.SMTP_HOST, Cfg.SMTP_PORT, context=ssl.create_default_context(), timeout=30)
    else:
        s = smtplib.SMTP(Cfg.SMTP_HOST, Cfg.SMTP_PORT, timeout=30)
        if Cfg.SMTP_SECURITY == "starttls": s.starttls(context=ssl.create_default_context())
    try:
        if Cfg.SMTP_USER: s.login(Cfg.SMTP_USER, Cfg.SMTP_PASS)
        s.send_message(msg)
    finally:
        s.quit()


def send_job(c, jid, user, event_id, cert_ids):
    ev = c.execute("SELECT e.*, d.name_ar dept_ar FROM events e JOIN departments d ON d.id=e.dept_id WHERE e.id=?", (event_id,)).fetchone()
    attach = setting(c, "attach_pdf", "0") == "1"
    for cid in cert_ids:
        cert = c.execute("SELECT * FROM certificates WHERE id=? AND status='valid'", (cid,)).fetchone()
        if not cert: _tick(c, jid); continue
        p = c.execute("SELECT * FROM participants WHERE id=?", (cert["participant_id"],)).fetchone()
        email = dec(p["email_enc"])
        if not EMAIL_RE.match(email):
            c.execute("UPDATE certificates SET email_status='failed', email_error='لا يوجد بريد صالح' WHERE id=?", (cid,)); _tick(c, jid, False); continue
        try:
            send_cert_email(c, cert, email, dec(cert["name_enc"]), ev, attach)
            c.execute("UPDATE certificates SET email_status='sent', email_sent_at=?, email_error='' WHERE id=?", (int(time.time()), cid)); _tick(c, jid)
            time.sleep(0.2)
        except Exception as e:
            c.execute("UPDATE certificates SET email_status='failed', email_error=? WHERE id=?", (type(e).__name__, cid)); _tick(c, jid, False)
    n = c.execute("SELECT done-failed ok, failed FROM jobs WHERE id=?", (jid,)).fetchone()
    audit("certs.email", f"event:{event_id}", f"ok={n['ok']} failed={n['failed']}", conn=c, user=user)
    return f"أُرسلت {n['ok']} رسالة" + (f"، وفشل {n['failed']}" if n["failed"] else "")


# ================= Excel =================
HEADERS = {
    "name": ["الاسم الكامل", "الاسم", "الاسم بالعربي", "name", "full name", "fullname", "الاسم (عربي)"],
    "name_en": ["name (english)", "english name", "الاسم بالانجليزي", "الاسم بالإنجليزي", "الاسم (إنجليزي)", "الاسم بالإنجليزية", "name_en"],
    "uid": ["الرقم الجامعي", "رقم جامعي", "university id", "student id", "id", "الرقم"],
    "email": ["البريد الإلكتروني", "البريد الالكتروني", "البريد", "email", "e-mail", "ايميل", "الايميل"],
    "spec": ["التخصص", "specialization", "major", "program", "القسم"],
    "acad": ["الحالة الأكاديمية", "الحالة الاكاديمية", "الحالة", "academic status", "status", "student status"],
    "gender": ["الجنس", "gender", "sex", "النوع"],
    "nat": ["الجنسية", "nationality", "citizenship"],
}
_H = {}
for k, v in HEADERS.items():
    for x in v: _H[re.sub(r"\s+", " ", x.strip().lower())] = k


def build_template_xlsx():
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "المشاركون"; ws.sheet_view.rightToLeft = True
    head = ["الاسم الكامل", "Name (English)", "الرقم الجامعي", "البريد الإلكتروني", "التخصص", "الحالة الأكاديمية", "الجنس", "الجنسية"]
    ws.append(head)
    ws.append(["اسم المشارك", "Participant Name", "00000000", "participant@example.com", "اسم التخصص", "طالب", "ذكر", "عماني"])
    fill = PatternFill("solid", fgColor="29328D")
    for i, c in enumerate(ws[1], 1):
        c.font = Font(bold=True, color="FFFFFF", name="Arial"); c.fill = fill; c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[c.column_letter].width = [28, 26, 18, 30, 24, 18, 12, 16][i - 1]
    for c in ws[2]:
        c.font = Font(color="888888", italic=True)
    ws.row_dimensions[1].height = 26
    for r in range(2, 502):
        ws.cell(r, 3).number_format = "@"
    for col, vals in (("F", "طالب,خريج"), ("G", "ذكر,أنثى"), ("H", "عماني,غير عماني")):  # قوائم منسدلة
        dv = DataValidation(type="list", formula1=f'"{vals}"', allow_blank=True, showErrorMessage=True,
                            errorTitle="قيمة غير صالحة", error="اختر قيمة من القائمة")
        ws.add_data_validation(dv); dv.add(f"{col}2:{col}501")
    ws2 = wb.create_sheet("تعليمات"); ws2.sheet_view.rightToLeft = True
    for line in ["• الأعمدة المطلوبة: الاسم الكامل، البريد الإلكتروني. الرقم الجامعي مطلوب إذا طُلب في الفعالية.",
                 "• الاسم الإنجليزي والتخصص اختياريان (يظهران في القوالب الرسمية ثنائية اللغة).",
                 "• الحالة الأكاديمية: طالب / خريج. الجنس: ذكر / أنثى. الجنسية: عماني / غير عماني (اختيار من قائمة؛ تُقبل أيضاً Student, Graduate, Male, Female, Omani, Non-Omani). يمكن تركها فارغة وإكمالها لاحقاً.",
                 "• احذف صف المثال (الرمادي) قبل الرفع.",
                 "• الحد الأقصى 5000 صف و5 ميجابايت. الصيغة المقبولة: .xlsx فقط.",
                 "• بيانات الفعالية (الاسم، التاريخ، الساعات، نوع الشهادة) تُحدَّد داخل النظام عند إنشاء الفعالية.",
                 "• يتحقق النظام من البريد والتكرار قبل الإصدار ويعرض لك الأخطاء لتصحيحها."]:
        ws2.append([line])
    ws2.column_dimensions["A"].width = 100
    b = io.BytesIO(); wb.save(b); return b.getvalue()


class ImportError_(Exception): pass


def parse_xlsx(data: bytes):
    if not data[:2] == b"PK": raise ImportError_("الملف ليس بصيغة xlsx صحيحة")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        if any("vbaProject" in n for n in names): raise ImportError_("الملفات التي تحتوي ماكرو غير مسموحة")
        if len(names) > 300 or sum(i.file_size for i in z.infolist()) > 60 * 1024 * 1024:
            raise ImportError_("الملف كبير بشكل غير طبيعي")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except ImportError_: raise
    except Exception:
        raise ImportError_("تعذّرت قراءة الملف. تأكد أنه xlsx سليم")
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if not header: raise ImportError_("الملف فارغ")
    cols = {}
    for i, h in enumerate(header):
        k = _H.get(re.sub(r"\s+", " ", str(h or "").strip().lower()))
        if k and k not in cols: cols[k] = i
    if "name" not in cols: raise ImportError_("لم أجد عمود «الاسم الكامل». استخدم قالب Excel الرسمي")
    rows = []
    for n, r in enumerate(it, start=2):
        if len(rows) >= Cfg.MAX_ROWS: raise ImportError_(f"الحد الأقصى {Cfg.MAX_ROWS} صف")
        g = lambda k: clean_text(r[cols[k]], 120) if k in cols and cols[k] < len(r) and r[cols[k]] is not None else ""
        if not any(g(k) for k in cols): continue
        uid = g("uid")
        if uid.endswith(".0") and uid[:-2].isdigit(): uid = uid[:-2]
        rows.append(dict(row=n, name=g("name"), name_en=g("name_en"), uid=uid, email=g("email").lower(), spec=g("spec"),
                         acad=g("acad"), gender=g("gender"), nat=g("nat")))
    wb.close()
    return rows


def validate_rows(rows, ev, existing_idx, strict=False):
    """يعيد (صالحة، أخطاء). existing_idx: مجموعة مفاتيح التكرار الموجودة في الفعالية.
    strict=True (نماذج التسجيل واليدوي): الحالة الأكاديمية والجنس والجنسية مطلوبة. في Excel اختيارية لكن القيمة الخاطئة تُرفض."""
    seen, good, bad = set(existing_idx), [], []
    for r in rows:
        errs = []
        for f in ("acad", "gender", "nat"):
            code = choices.norm(f, r.get(f, ""))
            if code is None: errs.append(f"«{choices.FIELDS[f]['ar']}» غير صالحة")
            elif code == "" and strict: errs.append(f"«{choices.FIELDS[f]['ar']}» مطلوبة")
            r[f] = code or ""
        if not valid_name(r["name"]): errs.append("الاسم غير صالح")
        if r["name_en"] and not valid_name(r["name_en"]): errs.append("الاسم الإنجليزي غير صالح")
        if r["email"] and not EMAIL_RE.match(r["email"]): errs.append("البريد غير صالح")
        if not r["email"]: errs.append("البريد مفقود")
        if ev["require_uid"] and not r["uid"]: errs.append("الرقم الجامعي مفقود")
        if r["uid"] and not UID_RE.match(r["uid"]): errs.append("الرقم الجامعي غير صالح")
        if ev["require_spec"] and not r["spec"]: errs.append("التخصص مفقود")
        key = blind("u", r["uid"]) if r["uid"] else blind("e", r["email"])
        if key and key in seen: errs.append("مكرر")
        if errs: bad.append(dict(r, errors=errs))
        else:
            seen.add(key); good.append(dict(r, key=key))
    return good, bad


def stage_save(user_id, payload):
    t = secrets.token_urlsafe(24)
    (DATA / "staging" / (t + ".enc")).write_bytes(enc_bytes(json.dumps(dict(u=user_id, t=int(time.time()), p=payload), ensure_ascii=False).encode()))
    return t


def stage_load(token, user_id):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,40}", token or ""): return None
    f = DATA / "staging" / (token + ".enc")
    if not f.exists(): return None
    d = json.loads(dec_bytes(f.read_bytes()))
    if d["u"] != user_id or time.time() - d["t"] > 3600: f.unlink(missing_ok=True); return None
    return d["p"]


def stage_drop(token):
    if re.fullmatch(r"[A-Za-z0-9_\-]{20,40}", token or ""): (DATA / "staging" / (token + ".enc")).unlink(missing_ok=True)


def cleanup_staging():
    for f in (DATA / "staging").glob("*.enc"):
        if time.time() - f.stat().st_mtime > 3600: f.unlink(missing_ok=True)


def send_plain(to_addr, subject, body, tag):
    msg = EmailMessage()
    msg["Subject"] = subject; msg["From"] = Cfg.SMTP_FROM; msg["To"] = to_addr; msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="injaz.local"); msg.set_content(body)
    if Cfg.MAIL_MODE == "file":
        (DATA / "outbox" / f"{tag}-{secrets.token_hex(4)}.eml").write_bytes(bytes(msg)); return
    if Cfg.SMTP_SECURITY == "ssl":
        s = smtplib.SMTP_SSL(Cfg.SMTP_HOST, Cfg.SMTP_PORT, context=ssl.create_default_context(), timeout=30)
    else:
        s = smtplib.SMTP(Cfg.SMTP_HOST, Cfg.SMTP_PORT, timeout=30)
        if Cfg.SMTP_SECURITY == "starttls": s.starttls(context=ssl.create_default_context())
    try:
        if Cfg.SMTP_USER: s.login(Cfg.SMTP_USER, Cfg.SMTP_PASS)
        s.send_message(msg)
    finally:
        s.quit()
