# common/tests/test_settings.py

"""The settings module refuses weak secret keys outside DEBUG. It is executed in isolation each time."""

import os
import runpy
import warnings
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

SETTINGS_FILE = str(settings.BASE_DIR / 'config' / 'settings.py')
STRONG_KEY = 'a-strong-unique-secret-key-0123456789-abcdefghij'


def load_settings(**env):
    with mock.patch.dict(os.environ, env, clear=False):
        return runpy.run_path(SETTINGS_FILE)


class SecretKeyGuardTests(SimpleTestCase):
    def test_strong_key_is_accepted(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            self.assertEqual(load_settings(SECRET_KEY=STRONG_KEY, DEBUG='False')['SECRET_KEY'], STRONG_KEY)

    def test_short_key_is_refused_outside_debug(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'shorter than 32 characters'):
            load_settings(SECRET_KEY='too-short', DEBUG='False')

    def test_short_key_only_warns_in_debug(self):
        with self.assertWarnsMessage(UserWarning, 'shorter than 32 characters'):
            load_settings(SECRET_KEY='too-short', DEBUG='True')

    def test_the_error_explains_how_to_fix_it(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'get_random_secret_key'):
            load_settings(SECRET_KEY='too-short', DEBUG='False')
