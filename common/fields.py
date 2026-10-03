# common/fields.py

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models


def _fernet():
    """
    Build the cipher from ``settings.FIELD_ENCRYPTION_KEYS`` (list of Fernet keys; the first
    encrypts, all of them can decrypt, which allows key rotation). Falls back to a key derived
    from SECRET_KEY so development works without extra configuration.
    """
    keys = list(getattr(settings, 'FIELD_ENCRYPTION_KEYS', []) or [])
    if not keys:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        keys = [base64.urlsafe_b64encode(digest).decode()]
    return MultiFernet([Fernet(key.encode() if isinstance(key, str) else key) for key in keys])


class EncryptedTextField(models.TextField):
    """
    Text stored encrypted at rest (Fernet). Transparent to Python code.

    Values are not deterministic, so you cannot filter or order by this field. Rows written
    before the field was encrypted are still readable and are encrypted the next time they are saved.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return None
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return value  # legacy plaintext
