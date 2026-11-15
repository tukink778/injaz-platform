"""طبقة HTTP الوحيدة للمزودين: HTTPS فقط، تحقق من الشهادات، مهلة، حد حجم، بلا تتبّع للتحويلات (حتى لا تتسرب الرموز)."""
import json, urllib.error, urllib.parse, urllib.request
from collections import namedtuple
from .errors import ProviderError

Resp = namedtuple("Resp", "status body retry_after")
MAX_BYTES = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None


_opener = urllib.request.build_opener(_NoRedirect)


def request(method, url, *, headers=None, form=None, json_body=None, timeout=20) -> Resp:
    if not url.startswith("https://"):
        raise ValueError("HTTPS only")
    data, h = None, {"Accept": "application/json", "User-Agent": "INJAZ-mailer/1.0"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        data = json.dumps(json_body).encode(); h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    ra = 0
    try:
        with _opener.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read(MAX_BYTES + 1)
        try: ra = int(e.headers.get("Retry-After", "0"))
        except (ValueError, TypeError): ra = 0
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("network", retryable=True)
    try: body = json.loads(raw.decode("utf-8")) if raw and len(raw) <= MAX_BYTES else {}
    except ValueError: body = {}
    return Resp(status, body if isinstance(body, dict) else {}, ra)
