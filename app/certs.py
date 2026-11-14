"""محرك الشهادات: قوالب مطابقة للنموذج المعتمد + قوالب إضافية، وتحويل HTML إلى PDF عبر Chromium."""
from . import choices
import base64, html, io, pathlib, re, threading, time, uuid
import jinja2, qrcode
from markupsafe import Markup
from PIL import Image
from . import config

HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE / "static"
ASSETS = config.DATA / "assets"
ASSETS.mkdir(exist_ok=True)

ORG_AR = "مركز التدريب والتوجيه الوظيفي — جامعة التقنية والعلوم التطبيقية بصحار"
ORG_EN = "Training and Career Guidance Center — University of Technology and Applied Sciences, Suhar"

DEFAULT_CFG = dict(layout="table", title_ar="", title_en="", intro_ar="", intro_en="", body_ar="", body_en="",
                   closing_ar="", closing_en="", primary="#29328D", accent="#E66926", background="official",
                   show_uid=True, show_spec=True, show_stamp=True, show_signature=True, show_qr=True,
                   orientation="landscape", elements=[])

INTRO_AR = "يشهد مركز التدريب والتوجيه الوظيفي بجامعة التقنية والعلوم التطبيقية بصحار بأن"
INTRO_EN = "The Training and Career Guidance Center at UTAS, Suhar, certifies that"

def _T(x, y, w, h, text, size, color="#14163a", weight=500, align="center", font="plex", **k):
    return dict(type="text", x=x, y=y, w=w, h=h, text=text, size=size, color=color, weight=weight, align=align, font=font, **k)


def modern_elements(portrait=False):
    P, A = "#1E2A78", "#7A4DFF"
    if not portrait:
        return [dict(type="rect", x=0, y=0, w=3.2, h=100, fill=P), dict(type="rect", x=3.2, y=0, w=.8, h=100, fill=A),
                _T(10, 9, 82, 13, "شهادة إنجاز", 34, P, 700, font="amiri", kashida=False),
                _T(10, 22, 82, 6, "CERTIFICATE OF ACHIEVEMENT", 13, A, 600, font="inter", dir="ltr", ls=4),
                dict(type="rect", x=44, y=30, w=12, h=.6, fill=A),
                _T(10, 33, 82, 7, "تشهد {dept_ar} بأن", 14, "#444444"),
                _T(10, 41, 82, 14, "{name_ar}", 34, P, 700, font="amiri"), _T(10, 55, 82, 7, "{name_en}", 16, A, 600, font="inter", dir="ltr"),
                _T(12, 65, 78, 10, "قد أتمّ بنجاح {event_ar} ({hours} ساعة) — {date_ar}", 14, "#333333", 500, lh=1.6),
                _T(12, 75, 78, 7, "has successfully completed {event_en} ({hours} hours) — {date_en}", 10.5, "#666666", 400, font="inter", dir="ltr"),
                dict(type="rect", x=14, y=87, w=22, h=.25, fill="#999999"), _T(14, 88, 22, 5, "{signatory}", 12, P, 700), _T(14, 92, 22, 4, "{signatory_title_ar}", 9.5, "#555555", 400),
                dict(type="qr", x=80, y=77, w=10.3, h=14.6, caption=True)]
    return [dict(type="rect", x=0, y=0, w=100, h=2.4, fill=P), dict(type="rect", x=0, y=2.4, w=100, h=.6, fill=A),
            _T(8, 9, 84, 8, "شهادة إنجاز", 34, P, 700, font="amiri"), _T(8, 17, 84, 4, "CERTIFICATE OF ACHIEVEMENT", 11, A, 600, font="inter", dir="ltr", ls=4),
            dict(type="rect", x=42, y=23, w=16, h=.2, fill=A), _T(8, 27, 84, 4, "تشهد {dept_ar} بأن", 13, "#444444"),
            _T(8, 33, 84, 8, "{name_ar}", 30, P, 700, font="amiri"), _T(8, 41, 84, 4, "{name_en}", 15, A, 600, font="inter", dir="ltr"),
            _T(10, 49, 80, 10, "قد أتمّ بنجاح {event_ar} ({hours} ساعة) — {date_ar}", 13, "#333333", 500, lh=1.7),
            _T(10, 60, 80, 6, "has successfully completed {event_en} ({hours} hours) — {date_en}", 10, "#666666", 400, font="inter", dir="ltr"),
            dict(type="rect", x=12, y=84, w=30, h=.15, fill="#999999"), _T(12, 85, 30, 3, "{signatory}", 11.5, P, 700), _T(12, 88, 30, 2.5, "{signatory_title_ar}", 9, "#555555", 400),
            dict(type="qr", x=70, y=79, w=18, h=12.8, caption=True)]



PRESETS = [
    ("رسمي — التدريب على رأس العمل", dict(layout="table", title_ar="شهادة التدريب على رأس العمل",
        title_en="Certificate of Completion On Job Training", intro_ar=INTRO_AR, intro_en=INTRO_EN,
        closing_ar="قد أتم بنجاح التدريب على رأس العمل", closing_en="He/She has successfully completed on-the-job training")),
    ("رسمي — الكفاءة المهنية", dict(layout="table", title_ar="شهادة الكفاءة المهنية",
        title_en="Certificate of Professional Competence", intro_ar=INTRO_AR, intro_en=INTRO_EN,
        closing_ar="وقد اجتاز بنجاح متطلبات الكفاءة المهنية", closing_en="and has successfully met the requirements of professional competence")),
    ("رسمي — شهادة تدريب (ثنائية اللغة)", dict(layout="paragraph", title_ar="شهادة تدريب", title_en="Training Certificate",
        intro_ar=INTRO_AR, body_ar="قد أتم بنجاح {event_ar} لمدة {hours} ساعة تدريبية، في الفترة {date_ar}.",
        body_en="has successfully completed {event_en} ({hours} training hours), {date_en}.", show_uid=False, show_spec=False)),
    ("رسمي — شهادة مشاركة", dict(layout="paragraph", title_ar="شهادة مشاركة", title_en="Certificate of Participation",
        intro_ar=INTRO_AR, body_ar="على مشاركته في {event_ar} المنعقدة بتاريخ {date_ar}.",
        body_en="for participating in {event_en} held on {date_en}.", show_uid=False, show_spec=False)),
    ("رسمي — شهادة حضور", dict(layout="paragraph", title_ar="شهادة حضور", title_en="Certificate of Attendance",
        intro_ar=INTRO_AR, body_ar="على حضوره {event_ar} بتاريخ {date_ar} ({hours} ساعة).",
        body_en="for attending {event_en} on {date_en} ({hours} hours).", show_uid=False, show_spec=False)),
    ("رسمي — تدريب عملي (إنجليزي كنموذج المرجع)", dict(layout="paragraph", title_ar="", title_en="Training Certificate",
        intro_en="The Training and Career Guidance Center at the University of Technology and Applied Sciences, Suhar, hereby certifies that,",
        body_en="has successfully completed the practical training at {event_en} From {date_from_en} To {date_to_en}.", show_uid=False, show_spec=False)),
    ("حديث — شهادة إنجاز (أفقي)", dict(layout="custom", orientation="landscape", background="plain", elements=modern_elements(False))),
    ("حديث — شهادة إنجاز (عمودي)", dict(layout="custom", orientation="portrait", background="plain", elements=modern_elements(True))),
    ("أنيق — شهادة تقدير (إطار ذهبي)", dict(layout="elegant", title_ar="شهادة تقدير", title_en="Certificate of Appreciation",
        intro_ar="يتشرف مركز التدريب والتوجيه الوظيفي بجامعة التقنية والعلوم التطبيقية بصحار بمنح هذه الشهادة إلى",
        body_ar="تقديراً لمشاركته المتميزة في {event_ar} بتاريخ {date_ar}.", body_en="In recognition of outstanding participation in {event_en}, {date_en}.",
        primary="#29328D", accent="#B8892B", show_uid=False, show_spec=False)),
]


# ---------- أدوات النص ----------
_NOCONN = set("اأإآدذرزوؤءةى")
_AR = re.compile(r"[؀-ۿ]")
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def is_arabic(s): return bool(_AR.search(s or ""))


def kashida(text, n=3):
    out = []
    words = (text or "").split(" ")
    for w in words:
        s = ""
        for i, ch in enumerate(w):
            s += ch
            if i < len(w) - 1 and "ء" <= ch <= "ي" and ch not in _NOCONN and "ء" <= w[i + 1] <= "ي":
                s += "ـ" * n
        out.append(s)
    return " ".join(out)


def _d(s):
    y, m, d = (int(x) for x in s.split("-"))
    return y, m, d


def fmt_date(s, lang):
    y, m, d = _d(s)
    return f"{d} {AR_MONTHS[m-1]} {y}" if lang == "ar" else f"{EN_MONTHS[m-1]} {d}, {y}"


def fmt_range(a, b, lang):
    if not b or b == a:
        return fmt_date(a, lang)
    return (f"من {fmt_date(a,'ar')} إلى {fmt_date(b,'ar')}" if lang == "ar" else f"from {fmt_date(a,'en')} to {fmt_date(b,'en')}")


def fmt_hours(h):
    return str(int(h)) if float(h).is_integer() else f"{h:g}"


def fill(text, ctx, upper=False):
    """يستبدل {الحقول} بقيم مُهرَّبة ويبرز القيم. كل نص يُهرَّب (لا HTML من المستخدم).
    upper: تحويل النص والقيم لأحرف كبيرة *قبل* التهريب حتى لا تتلف وسوم HTML وأسماء الأصناف."""
    u = (lambda v: v.upper()) if upper else (lambda v: v)
    out = []
    for part in re.split(r"(\{\w+\})", text or ""):
        m = re.fullmatch(r"\{(\w+)\}", part)
        if m and m.group(1) in ctx:
            k = m.group(1); cls = "bd" if k.startswith(("event", "name")) else "hl"
            out.append(f'<span class="{cls}">{html.escape(u(str(ctx[k])))}</span>')
        else:
            out.append(html.escape(u(part)))
    return Markup("".join(out))


_env = jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "certs"), autoescape=True)
_env.filters["kashida"] = lambda s, n=3: kashida(s, n)
_env.filters["fill"] = fill


def qr_data_uri(url):
    img = qrcode.QRCode(box_size=8, border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    img.add_data(url); img.make(fit=True)
    b = io.BytesIO(); img.make_image(fill_color="#111", back_color="white").save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


FONTS = {"plex": "Plex,sans-serif", "amiri": "AmiriC,serif", "inter": "Inter,Plex,sans-serif"}
BUILTIN_IMG = ("stamp", "signature", "logo_univ", "logo_pixels")
_ASSET_RE = re.compile(r"[0-9a-f]{32}\.(png|jpg)")
PLACEHOLDERS = ["name", "name_ar", "name_en", "uid", "specialization", "event_ar", "event_en", "date_ar", "date_en", "date_from_en", "date_to_en",
                "hours", "dept_ar", "dept_en", "serial", "signatory", "signatory_title_ar", "signatory_title_en", "location", "issue_date_ar", "issue_date_en",
                "academic_status_ar", "academic_status_en", "gender_ar", "gender_en", "nationality_ar", "nationality_en"]


def _num(v, lo, hi, dflt):
    try: return max(lo, min(hi, round(float(v), 2)))
    except (TypeError, ValueError): return dflt


def _color(v, dflt="#111111"):
    return v if re.fullmatch(r"#[0-9a-fA-F]{6}", str(v or "")) else dflt


def clean_element(e: dict, i: int = 0) -> dict:
    t = e.get("type") if e.get("type") in ("text", "image", "qr", "rect") else "text"
    out = dict(id=re.sub(r"[^a-z0-9_]", "", str(e.get("id") or f"e{i}").lower())[:16] or f"e{i}", type=t,
               x=_num(e.get("x"), -20, 120, 10), y=_num(e.get("y"), -20, 120, 10), w=_num(e.get("w"), 1, 140, 30), h=_num(e.get("h"), 0.3, 140, 8))
    if t == "text":
        out.update(text=str(e.get("text", ""))[:800], font=e.get("font") if e.get("font") in FONTS else "plex", size=_num(e.get("size"), 5, 120, 16),
                   weight=int(_num(e.get("weight"), 300, 800, 500) // 100 * 100), color=_color(e.get("color")), align=e.get("align") if e.get("align") in ("right", "center", "left", "justify") else "center",
                   dir=e.get("dir") if e.get("dir") in ("rtl", "ltr") else "rtl", valign=e.get("valign") if e.get("valign") in ("top", "middle", "bottom") else "middle",
                   lh=_num(e.get("lh"), 0.9, 3, 1.4), ls=_num(e.get("ls"), -2, 20, 0), kashida=bool(e.get("kashida")), upper=bool(e.get("upper")))
    elif t == "image":
        src = str(e.get("src", "stamp"))
        out.update(src=src if src in BUILTIN_IMG or _ASSET_RE.fullmatch(src) else "stamp", opacity=_num(e.get("opacity"), 0.05, 1, 1))
    elif t == "qr":
        out.update(caption=bool(e.get("caption", True)), color=_color(e.get("color"), "#111111"))
    else:
        out.update(fill=_color(e.get("fill"), "#29328D"), radius=_num(e.get("radius"), 0, 50, 0), opacity=_num(e.get("opacity"), 0.05, 1, 1),
                   border=_num(e.get("border"), 0, 20, 0), bcolor=_color(e.get("bcolor"), "#29328D"))
    return out


def clean_cfg(raw: dict) -> dict:
    cfg = dict(DEFAULT_CFG)
    for k, v in (raw or {}).items():
        if k in DEFAULT_CFG:
            cfg[k] = v
    if cfg["layout"] not in ("table", "paragraph", "elegant", "custom"):
        cfg["layout"] = "table"
    cfg["orientation"] = "portrait" if cfg.get("orientation") == "portrait" else "landscape"
    for k in ("primary", "accent"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(cfg[k])):
            cfg[k] = DEFAULT_CFG[k]
    for k in ("title_ar", "title_en", "intro_ar", "intro_en", "body_ar", "body_en", "closing_ar", "closing_en"):
        cfg[k] = str(cfg[k])[:600]
    for k in ("show_uid", "show_spec", "show_stamp", "show_signature", "show_qr"):
        cfg[k] = bool(cfg[k])
    els = cfg.get("elements") or []
    cfg["elements"] = [clean_element(e, i) for i, e in enumerate(els[:60] if isinstance(els, list) else []) if isinstance(e, dict)]
    bg = str(cfg["background"])
    if bg not in ("official", "plain") and not re.fullmatch(r"[0-9a-f]{32}\.jpg", bg) and not re.fullmatch(r"#[0-9a-fA-F]{6}", bg):
        bg = "official"
    cfg["background"] = bg
    return cfg


def page_size(cfg) -> tuple:
    return (210, 297) if (cfg or {}).get("orientation") == "portrait" else (297, 210)


def save_asset(fileobj) -> str:
    """صورة شعار/ختم/توقيع: يُعاد ترميزها PNG (مع الشفافية) وتُحفظ باسم عشوائي."""
    fileobj.seek(0)
    im = Image.open(fileobj); im.verify(); fileobj.seek(0)
    im = Image.open(fileobj)
    if im.width * im.height > 40_000_000: raise ValueError("الصورة كبيرة جداً")
    im = im.convert("RGBA"); im.thumbnail((1600, 1600))
    name = uuid.uuid4().hex + ".png"
    im.save(ASSETS / name, optimize=True)
    return name


def save_background(fileobj) -> str:
    """يتحقق من الصورة ويعيد ترميزها بالكامل (يزيل أي بيانات مخفية) ويحفظها باسم عشوائي."""
    fileobj.seek(0)
    im = Image.open(fileobj)
    im.verify(); fileobj.seek(0)
    im = Image.open(fileobj).convert("RGB")
    if im.width < 1000:
        raise ValueError("الصورة صغيرة (الحد الأدنى 1000 بكسل عرضاً)")
    im.thumbnail((2339, 1654))
    name = uuid.uuid4().hex + ".jpg"
    im.save(ASSETS / name, quality=88, optimize=True)
    return name


def _bg_url(cfg):
    b = cfg["background"]
    if b == "official": return "file://" + str(STATIC / "img/cert_bg.jpg")
    if b == "plain" or b.startswith("#"): return ""
    return "file://" + str(ASSETS / b)


def _render_elements(cfg, ctx, serial, verify_url, S):
    """يحوّل عناصر المحرر المرئي إلى HTML جاهز. كل القيم مُنقّاة مسبقاً في clean_cfg."""
    out = []
    qr = None
    for e in cfg["elements"]:
        box = f"left:{e['x']}%;top:{e['y']}%;width:{e['w']}%;height:{e['h']}%;"
        if e["type"] == "text":
            txt = e["text"]
            if e["kashida"]: txt = kashida(txt, 2)
            h = fill(txt, ctx, bool(e["upper"]))
            jv = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}[e["valign"]]
            st = (box + f"display:flex;flex-direction:column;justify-content:{jv};font-family:{FONTS[e['font']]};font-size:{e['size']}pt;font-weight:{e['weight']};color:{e['color']};"
                  f"text-align:{e['align']};direction:{e['dir']};line-height:{e['lh']};letter-spacing:{e['ls']}px;")
            out.append(dict(kind="text", style=st, html=Markup(f'<div style="white-space:pre-wrap">{h}</div>')))
        elif e["type"] == "image":
            src = S("img/" + e["src"] + ".png") if e["src"] in BUILTIN_IMG else "file://" + str(ASSETS / e["src"])
            out.append(dict(kind="img", style=box + f"opacity:{e['opacity']};", src=src))
        elif e["type"] == "qr":
            out.append(dict(kind="qr", style=box + "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1mm;", src=qr_data_uri(verify_url), caption=e["caption"], serial=serial))
        else:
            st = box + f"background:{e['fill']};opacity:{e['opacity']};border-radius:{e['radius']}mm;" + (f"border:{e['border']}mm solid {e['bcolor']};" if e["border"] else "")
            out.append(dict(kind="rect", style=st))
    return out


def build_html(cfg, ev, part, serial, verify_url, org):
    cfg = clean_cfg(cfg)
    name, name_en = part["name"], part.get("name_en", "")
    names_ar = name if is_arabic(name) else ""
    names_en = name_en or (name if not is_arabic(name) else "")
    if is_arabic(name) and name_en and not is_arabic(name_en):
        names_ar, names_en = name, name_en
    ctx = dict(name_ar=names_ar, name_en=names_en, name=names_ar or names_en, uid=part.get("uid", ""),
               specialization=part.get("spec", ""), event_ar=ev["title_ar"], event_en=ev["title_en"] or ev["title_ar"],
               date_ar=fmt_range(ev["event_date"], ev["end_date"], "ar"), date_en=fmt_range(ev["event_date"], ev["end_date"], "en"),
               date_from_en=fmt_date(ev["event_date"], "en"), date_to_en=fmt_date(ev["end_date"] or ev["event_date"], "en"),
               academic_status_ar=choices.label("acad", part.get("acad", ""), "ar") if part.get("acad") else "", academic_status_en=choices.label("acad", part.get("acad", ""), "en") if part.get("acad") else "",
               gender_ar=choices.label("gender", part.get("gender", ""), "ar") if part.get("gender") else "", gender_en=choices.label("gender", part.get("gender", ""), "en") if part.get("gender") else "",
               nationality_ar=choices.label("nat", part.get("nat", ""), "ar") if part.get("nat") else "", nationality_en=choices.label("nat", part.get("nat", ""), "en") if part.get("nat") else "",
               hours=fmt_hours(ev["hours"]), dept_ar=org["dept_ar"], dept_en=org["dept_en"], serial=serial,
               signatory=ev["signatory_name"], signatory_title_ar=ev["signatory_title_ar"], signatory_title_en=ev["signatory_title_en"],
               location=ev["location"] if "location" in ev.keys() else "", issue_date_ar=fmt_date(time.strftime("%Y-%m-%d"), "ar"), issue_date_en=fmt_date(time.strftime("%Y-%m-%d"), "en"))
    cfg["bg_url"] = _bg_url(cfg)
    cfg["bg_color"] = cfg["background"] if cfg["background"].startswith("#") else ""
    pw, ph = page_size(cfg)
    S = lambda p: "file://" + str(STATIC / p)
    cfg["els"] = _render_elements(cfg, ctx, serial, verify_url, S) if cfg["layout"] == "custom" else []
    tpl = _env.get_template(cfg["layout"] + ".html")
    return tpl.render(cfg=cfg, ctx=ctx, serial=serial, fonts=str(STATIC / "fonts"), pw=pw, ph=ph, names_ar=names_ar, names_en=names_en,
                      uid=part.get("uid", ""), spec=part.get("spec", ""), qr_url=qr_data_uri(verify_url),
                      stamp_url=S("img/stamp.png"), sig_url=S("img/signature.png"), logo_univ_url=S("img/logo_univ.png"),
                      signatory_name=ev["signatory_name"], signatory_title_en=ev["signatory_title_en"],
                      dept_en=org["dept_en"], org_en=org["org_en"])


# ---------- Chromium ----------
class Renderer:
    """يفتح المتصفح مرة واحدة لدفعة كاملة. يُستخدم داخل خيط واحد."""
    _lock = threading.Lock()

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        Renderer._lock.acquire()
        self._pw = sync_playwright().start()
        args = ["--no-sandbox"] if config.Cfg.NOSANDBOX else []
        self._b = self._pw.chromium.launch(args=args)
        return self

    def pdf(self, html_text: str, size=(297, 210)) -> bytes:
        tmp = config.DATA / "tmp" / (uuid.uuid4().hex + ".html")
        tmp.write_text(html_text, encoding="utf8")
        try:
            page = self._b.new_page()
            page.goto("file://" + str(tmp))
            page.evaluate("document.fonts.ready")
            data = page.pdf(width=f"{size[0]}mm", height=f"{size[1]}mm", print_background=True, prefer_css_page_size=True)
            page.close()
            return data
        finally:
            tmp.unlink(missing_ok=True)

    def png(self, html_text: str, size=(297, 210)) -> bytes:
        tmp = config.DATA / "tmp" / (uuid.uuid4().hex + ".html")
        tmp.write_text(html_text, encoding="utf8")
        try:
            page = self._b.new_page(viewport={"width": round(size[0] * 96 / 25.4), "height": round(size[1] * 96 / 25.4)}, device_scale_factor=1)
            page.goto("file://" + str(tmp)); page.evaluate("document.fonts.ready")
            data = page.screenshot(type="png")
            page.close()
            return data
        finally:
            tmp.unlink(missing_ok=True)

    def __exit__(self, *a):
        try:
            self._b.close(); self._pw.stop()
        finally:
            Renderer._lock.release()


def org_for_sample():
    return dict(dept_ar="مركز التدريب والتوجيه الوظيفي", dept_en="Training and Career Guidance Center", org_en="University of Technology and Applied Sciences, Suhar")
