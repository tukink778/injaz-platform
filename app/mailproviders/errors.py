"""أخطاء مزودي البريد. رسائلها رموز قصيرة آمنة فقط (لا نصوص ردود ولا رموز مصادقة)."""
import re


def safe_code(v, default="unknown"):
    s = re.sub(r"[^A-Za-z0-9_.\-]", "", str(v or ""))[:48]
    return s or default


class ProviderError(Exception):
    def __init__(self, code="error", retryable=False, retry_after=0):
        super().__init__(safe_code(code)); self.code = safe_code(code); self.retryable = retryable; self.retry_after = retry_after


class AuthExpired(ProviderError):
    """رمز التجديد مرفوض/ملغى (invalid_grant…): لا حل إلا إعادة الربط من المستخدم."""


class TokenRejected(ProviderError):
    """رمز الوصول مرفوض (401): يُجدَّد مرة واحدة ثم يُعاد الإرسال."""
