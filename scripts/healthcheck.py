"""
Container healthcheck: request /healthz/ and exit 0 if it answers 200.

/healthz/ is answered before Django's host check (common/health.py), so no Host header is needed.
"""

import os
import sys
import urllib.request

port = os.environ.get('PORT', '8000')

try:
    urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz/', timeout=3)
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(f'unhealthy: {exc}', file=sys.stderr)
    sys.exit(1)
