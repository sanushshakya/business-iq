"""
Container healthcheck: request the admin login page and exit 0 if it answers.

Django rejects requests whose Host header is not in ALLOWED_HOSTS, so the check sends the first
configured host instead of assuming "localhost".
"""

import os
import re
import sys
import urllib.request

hosts = [h for h in re.split(r'[,\s]+', os.environ.get('ALLOWED_HOSTS', '')) if h]
host = next((h.lstrip('.') for h in hosts if h != '*'), 'localhost')
port = os.environ.get('PORT', '8000')

request = urllib.request.Request(f'http://127.0.0.1:{port}/admin/login/', headers={'Host': host})
try:
    urllib.request.urlopen(request, timeout=3)
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(f'unhealthy: {exc}', file=sys.stderr)
    sys.exit(1)
