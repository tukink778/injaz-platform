"""الواجهة الموحدة لمزودي البريد. لإضافة مزوّد جديد: صنف يرث MailProvider ويُسجَّل بـ @register (انظر docs/EMAIL_INTEGRATION.md)."""
import base64, hashlib, os
from dataclasses import dataclass
from . import http
from .errors import AuthExpired, ProviderError, TokenRejected, safe_code


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def pkce_challenge(verifier: str) -> str:
    return b64url(hashlib.sha256(verifier.encode()).digest())


@dataclass
class Tokens:
    access_token: str
    refresh_token: str = ""
    expires_in: int = 3600
    scope: str = ""


@dataclass
class Identity:
    subject: str
    email: str


_REVOKED_GRANT = {"invalid_grant", "interaction_required", "consent_required", "login_required", "unauthorized_client", "invalid_client"}


class MailProvider:
    key = ""; label_ar = ""; label_en = ""
    scopes: list = []            # الصلاحيات المطلوبة (أقل ما يلزم)
    send_scopes: list = []       # أي منها يكفي لإثبات صلاحية الإرسال
    env_id = ""; env_secret = ""
    min_interval = 0.3           # أقل فاصل بين رسالتين (ثوانٍ) احتراماً لحدود المزوّد
    token_url = ""

    # ---- الإعداد ----
    def client_id(self): return os.environ.get(self.env_id, "")
    def client_secret(self): return os.environ.get(self.env_secret, "")
    def configured(self): return bool(self.client_id() and self.client_secret())

    def has_send_scope(self, granted: str) -> bool:
        g = {s.lower() for s in (granted or "").split()}
        return any(s.lower() in g for s in self.send_scopes)

    # ---- تدفق OAuth ----
    def authorize_url(self, redirect_uri, state, code_challenge) -> str: raise NotImplementedError
    def identity(self, access_token) -> Identity: raise NotImplementedError
    def send(self, access_token, msg) -> None: raise NotImplementedError
    def revoke(self, token) -> None: return None   # أفضل جهد؛ بعض المزودين لا يوفّرون إلغاء لكل رمز

    def exchange_code(self, code, redirect_uri, code_verifier) -> Tokens:
        return self._token_call(dict(grant_type="authorization_code", code=code, redirect_uri=redirect_uri, code_verifier=code_verifier))

    def refresh(self, refresh_token) -> Tokens:
        return self._token_call(dict(grant_type="refresh_token", refresh_token=refresh_token))

    def _token_call(self, form) -> Tokens:
        form = dict(form, client_id=self.client_id(), client_secret=self.client_secret())
        r = http.request("POST", self.token_url, form=form)
        if r.status == 200 and r.body.get("access_token"):
            return Tokens(r.body["access_token"], r.body.get("refresh_token", "") or "", int(r.body.get("expires_in", 3600) or 3600), r.body.get("scope", "") or "")
        err = safe_code(r.body.get("error"), f"http_{r.status}")
        if err in _REVOKED_GRANT: raise AuthExpired(err)
        raise ProviderError(err, retryable=r.status >= 500 or r.status == 429, retry_after=r.retry_after)

    def _check_send(self, r: http.Resp, ok=(200, 202, 204)):
        if r.status in ok: return
        if r.status == 401: raise TokenRejected("token_rejected")
        raise ProviderError(f"http_{r.status}", retryable=r.status >= 500 or r.status == 429, retry_after=r.retry_after)
