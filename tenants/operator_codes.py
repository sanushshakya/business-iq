# tenants/operator_codes.py

"""
Operators (till, demand, freight, ...) sign in with an 8-digit code instead of an email and password.

The code is the whole credential, so it is generated (never chosen: letting an admin pick one would reveal
which codes other companies already use), stored only as an HMAC, and shown to the admin once, when it is
created or reset. Codes are unique across all companies because the code alone identifies the user.
"""

import hashlib
import hmac
import re
import secrets

from django.conf import settings

CODE_LENGTH = 8
CODE_RE = re.compile(rf'^\d{{{CODE_LENGTH}}}$')


def generate_code():
    return f'{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}'


def hash_code(code):
    return hmac.new(settings.SECRET_KEY.encode(), f'till-code:{code}'.encode(), hashlib.sha256).hexdigest()


def is_valid_code(value):
    return isinstance(value, str) and bool(CODE_RE.match(value))
