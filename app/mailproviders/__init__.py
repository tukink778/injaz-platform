"""سجل مزودي البريد. لإضافة مزوّد: أنشئ ملفاً يعرّف صنفاً بـ @register ثم استورده في آخر هذا الملف."""
_REG: dict = {}


def register(cls):
    _REG[cls.key] = cls()
    return cls


def get(key):
    return _REG.get(key)


def all_providers():
    return list(_REG.values())


from . import microsoft, google  # noqa: E402,F401  (تسجيل المزودين)
