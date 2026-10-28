"""HMAC signing for the IQ <-> m18 storefront webhooks.

Header ``X-Integration-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>``.
Keep in step with the copy in the m18 repo (``shop/integration/signing.py``); the header parse is strict
on purpose. Always verify over the raw request body bytes, never a re-serialisation.
"""

import hashlib
import hmac
import time


def _mac(secret, t, body):
    return hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


def sign(secret, body, now=None):
    """Return the X-Integration-Signature header value for ``body``."""
    t = int(time.time()) if now is None else int(now)
    return f"t={t},v1={_mac(secret, t, body)}"


def verify(secret, body, header, now=None, tolerance=300):
    """True only if ``header`` is a fresh, valid signature of ``body``. Never raises."""
    try:
        if not secret or not header or not isinstance(header, str):
            return False
        parts = header.split(",")
        if len(parts) != 2:
            return False
        t_part, v_part = parts
        if not t_part.startswith("t=") or not v_part.startswith("v1="):
            return False
        t_str, given = t_part[2:], v_part[3:]
        if not (t_str.isascii() and t_str.isdigit()):
            return False
        if len(given) != 64 or any(c not in "0123456789abcdef" for c in given):
            return False
        t = int(t_str)
        current = int(time.time()) if now is None else int(now)
        if abs(current - t) > tolerance:
            return False
        return hmac.compare_digest(_mac(secret, t, body), given)
    except (TypeError, ValueError, AttributeError):
        return False
