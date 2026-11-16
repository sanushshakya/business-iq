"""
HMRC fraud-prevention headers (G56), connection method ``WEB_APP_VIA_SERVER``, spec v3.3:
https://developer.service.hmrc.gov.uk/guides/fraud-prevention/connection-method/web-app-via-server

The browser sends what only it knows (``ClientInfoSerializer``) in the request body; the server adds the public IP and
port it saw at the edge (CloudFront's ``CloudFront-Viewer-Address``), the user's last two-factor check, and the
vendor's own details. Data that can't be collected is sent empty (HMRC's "missing data" rule): a private client
address (local runs) and licence IDs (in-house software). Keys and values are percent-encoded, separators are not.
"""

import hashlib
import ipaddress
from decimal import Decimal
from datetime import UTC, datetime
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework.throttling import BaseThrottle

SERVER_PORTS = {80, 443}


class ScreenSerializer(serializers.Serializer):
    width = serializers.IntegerField(min_value=1, max_value=100000)
    height = serializers.IntegerField(min_value=1, max_value=100000)
    # Browsers report e.g. 1.100000023841858 at 110% zoom: any positive number, rounded to 3 places when sent.
    scaling_factor = serializers.FloatField(min_value=0.001, max_value=100)
    colour_depth = serializers.IntegerField(min_value=1, max_value=128)


class WindowSerializer(serializers.Serializer):
    width = serializers.IntegerField(min_value=1, max_value=100000)
    height = serializers.IntegerField(min_value=1, max_value=100000)


class ClientInfoSerializer(serializers.Serializer):
    """What the browser reports about itself for the fraud-prevention headers."""

    device_id = serializers.UUIDField()
    user_agent = serializers.CharField(max_length=500)
    screens = ScreenSerializer(many=True, allow_empty=False, max_length=6)
    window = WindowSerializer()
    timezone = serializers.RegexField(r'^UTC[+-]\d{2}:\d{2}$', error_messages={'invalid': 'Use UTC±hh:mm.'})
    web_version = serializers.CharField(max_length=64)


def _q(value):
    return quote(str(value), safe='')


# Printable US-ASCII except '%' stays as it is; anything else is percent-encoded (values passed "as reported").
_PRINTABLE = ''.join(chr(code) for code in range(32, 127) if chr(code) != '%')


def _ascii(value):
    return quote(str(value), safe=_PRINTABLE)


def _pairs(items):
    return '&'.join(f'{_q(key)}={_q(value)}' for key, value in items)


def _scale(value):
    """1.100000023841858 → '1.1', 2.0 → '2'."""
    return format(Decimal(str(round(value, 3))).normalize(), 'f')


def _public(ip):
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def client_address(request):
    """The browser's public IP and port, or (None, None) when only a private address is known. Behind CloudFront
    both come from ``CloudFront-Viewer-Address`` (``ip:port``, IPv6 in brackets); otherwise the IP DRF trusts
    (``NUM_PROXIES``) and no port."""
    viewer = request.META.get('HTTP_CLOUDFRONT_VIEWER_ADDRESS', '')
    if viewer:
        host, _, port = viewer.rpartition(':')
        host = host.strip('[]')
        if _public(host):
            port = int(port) if port.isdigit() and 0 < int(port) < 65536 else None
            return host, (None if port in SERVER_PORTS else port)
    ip = BaseThrottle().get_ident(request)
    return (ip, None) if _public(ip) else (None, None)


def edge_timestamp(request):
    """When the request reached us, in UTC with milliseconds; taken once per request."""
    if not hasattr(request, '_hmrc_received_at'):
        request._hmrc_received_at = timezone.now()
    moment = request._hmrc_received_at.astimezone(UTC)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + f'{moment.microsecond // 1000:03d}Z'


def _multi_factor(user):
    device = getattr(user, 'two_factor', None)  # RelatedObjectDoesNotExist is an AttributeError
    if device is None or device.confirmed_at is None or device.last_used_step is None:
        return ''
    when = datetime.fromtimestamp(device.last_used_step * 30, UTC).strftime('%Y-%m-%dT%H:%MZ')
    reference = hashlib.sha256(f'{device.pk}:{settings.SECRET_KEY}'.encode()).hexdigest()
    return _pairs([('type', 'TOTP'), ('timestamp', when), ('unique-reference', reference)])


def headers(request, client, user):
    """Every ``WEB_APP_VIA_SERVER`` header for one call to HMRC made on behalf of ``user`` from this request."""
    ip, port = client_address(request)
    vendor_ip = settings.HMRC_VENDOR_PUBLIC_IP
    return {
        'Gov-Client-Connection-Method': 'WEB_APP_VIA_SERVER',
        'Gov-Client-Browser-JS-User-Agent': _ascii(client['user_agent']),
        'Gov-Client-Device-ID': str(client['device_id']),
        'Gov-Client-Multi-Factor': _multi_factor(user),
        'Gov-Client-Public-IP': ip or '',
        'Gov-Client-Public-IP-Timestamp': edge_timestamp(request) if ip else '',
        'Gov-Client-Public-Port': str(port) if ip and port else '',
        'Gov-Client-Screens': ','.join(_pairs([
            ('width', screen['width']), ('height', screen['height']),
            ('scaling-factor', _scale(screen['scaling_factor'])), ('colour-depth', screen['colour_depth'])])
            for screen in client['screens']),
        'Gov-Client-Timezone': client['timezone'],
        'Gov-Client-User-IDs': _pairs([('m18-iq', user.pk)]),
        'Gov-Client-Window-Size': _pairs([('width', client['window']['width']),
                                          ('height', client['window']['height'])]),
        'Gov-Vendor-Forwarded': _pairs([('by', vendor_ip), ('for', ip)]) if ip and vendor_ip else '',
        'Gov-Vendor-License-IDs': '',
        'Gov-Vendor-Product-Name': _q(settings.HMRC_VENDOR_PRODUCT_NAME),
        'Gov-Vendor-Public-IP': vendor_ip,
        'Gov-Vendor-Version': _pairs([('m18-iq', settings.APP_VERSION), ('m18-iq-web', client['web_version'])]),
    }
