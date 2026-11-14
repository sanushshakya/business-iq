# common/tests/test_production_settings.py

"""Settings for running behind CloudFront and a load balancer (G60): all from the environment, all off by default."""

from django.test import SimpleTestCase, override_settings

from common.tests.test_settings import STRONG_KEY, load_settings


def prod(**env):
    return load_settings(SECRET_KEY=STRONG_KEY, DEBUG='False', **env)


class ProxySettingsTests(SimpleTestCase):
    def test_defaults_leave_local_runs_unchanged(self):
        s = prod(
            BEHIND_HTTPS_PROXY='', SECURE_HSTS_SECONDS='', CSRF_TRUSTED_ORIGINS='', DB_SSLMODE='',
            DB_ENGINE='django.db.backends.postgresql',
        )
        self.assertIsNone(s.get('SECURE_PROXY_SSL_HEADER'))
        self.assertFalse(s['SESSION_COOKIE_SECURE'])
        self.assertFalse(s['CSRF_COOKIE_SECURE'])
        self.assertEqual(s['SECURE_HSTS_SECONDS'], 0)
        self.assertEqual(s['CSRF_TRUSTED_ORIGINS'], [])
        self.assertNotIn('OPTIONS', s['DATABASES']['default'])

    def test_behind_an_https_proxy(self):
        s = prod(BEHIND_HTTPS_PROXY='True')
        self.assertEqual(s['SECURE_PROXY_SSL_HEADER'], ('HTTP_X_FORWARDED_PROTO', 'https'))
        self.assertTrue(s['SESSION_COOKIE_SECURE'])
        self.assertTrue(s['CSRF_COOKIE_SECURE'])

    def test_hsts(self):
        s = prod(SECURE_HSTS_SECONDS='31536000')
        self.assertEqual(s['SECURE_HSTS_SECONDS'], 31536000)
        self.assertFalse(s['SECURE_HSTS_INCLUDE_SUBDOMAINS'])

    def test_trusted_origins_are_a_list(self):
        s = prod(CSRF_TRUSTED_ORIGINS='https://iq.example.co.uk, https://staging.example.co.uk')
        self.assertEqual(s['CSRF_TRUSTED_ORIGINS'], ['https://iq.example.co.uk', 'https://staging.example.co.uk'])

    def test_database_ssl_mode(self):
        s = prod(DB_ENGINE='django.db.backends.postgresql', DB_SSLMODE='require')
        self.assertEqual(s['DATABASES']['default']['OPTIONS'], {'sslmode': 'require'})

    def test_health_check_middleware_runs_first(self):
        self.assertEqual(prod()['MIDDLEWARE'][0], 'common.health.HealthCheckMiddleware')


@override_settings(ALLOWED_HOSTS=['iq.example.co.uk'])
class HealthCheckTests(SimpleTestCase):
    # SimpleTestCase refuses database queries, so these also show the check never touches the database.

    def test_answers_with_any_host(self):
        response = self.client.get('/healthz/', HTTP_HOST='10.0.3.17:8000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')

    def test_answers_behind_an_https_proxy_over_plain_http(self):
        with override_settings(SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')):
            self.assertEqual(self.client.get('/healthz/', HTTP_HOST='10.0.3.17').status_code, 200)

    def test_other_paths_still_check_the_host(self):
        self.assertEqual(self.client.get('/admin/login/', HTTP_HOST='10.0.3.17').status_code, 400)

    def test_only_get_and_head(self):
        self.assertEqual(self.client.head('/healthz/', HTTP_HOST='x').status_code, 200)
        self.assertEqual(self.client.post('/healthz/', HTTP_HOST='x').status_code, 405)
