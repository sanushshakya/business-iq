# authentication/role_access.py

"""
What an operator (someone who signs in with an 8-digit code) may call, by role.

Each role is an allow-list checked on every request they make, so a new endpoint is closed to operators until it
is listed here. Tenancy still applies on top: they only ever reach their own company's data, and a viewset's own
permissions (for example staff-only writes to shared reference data) still say no where they say no.
"""

import re

from tenants.roles import DEMAND, FREIGHT, STOCK, SUPPLIER, TILL

READ = {'GET'}
WRITE = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}


def _collection(prefix, methods=WRITE):
    """``/prefix/`` (list, create) and ``/prefix/<id>/`` (detail); ``methods`` is what the role may do."""
    return [
        (re.compile(rf'^/{prefix}/$'), methods & {'GET', 'POST'}),
        (re.compile(rf'^/{prefix}/\d+/$'), methods - {'POST'}),
    ]


def _route(pattern, *methods):
    return [(re.compile(pattern), set(methods))]


COMMON = _route(r'^/auth/me/$', 'GET') + _route(r'^/tenants/company/$', 'GET')

PRODUCTS_READ = (
    _collection('inventory/products', READ)
    + _route(r'^/inventory/products/\d+/stock-projection/$', 'GET')
    + _collection('inventory/categories', READ)
)
STOCK_ALERTS = _route(r'^/common/api/alerts/stock/$', 'GET')

ROLE_ROUTES = {
    TILL: (
        _collection('tenants/tills', READ)
        + _collection('tenants/branches', READ)
        + _collection('inventory/products', READ)
        + _route(r'^/sales/sessions/$', 'GET', 'POST')
        + _route(r'^/sales/sessions/\d+/$', 'GET')
        + _route(r'^/sales/sessions/\d+/close/$', 'POST')
        + _route(r'^/sales/sales/$', 'GET', 'POST')
        + _route(r'^/sales/sales/\d+/$', 'GET')
        + _route(r'^/sales/sales/\d+/void/$', 'POST')
        + _route(r'^/sync/loyalty/lookup/$', 'POST')
    ),
    DEMAND: (
        PRODUCTS_READ
        + STOCK_ALERTS
        + _collection('demand/demands')
        + _collection('demand/events')
        + _collection('demand/keywords')
        + _collection('calendar/events')
        + _collection('calendar/alerts')
        + _route(r'^/calendar/upcoming/$', 'GET')
        + _route(r'^/common/api/alerts/demand/$', 'GET')
        + _route(r'^/common/api/alerts/demand/create/$', 'POST')
        + _route(r'^/common/api/alerts/demand/dismiss/\d+/$', 'POST')
    ),
    FREIGHT: (
        _collection('logistics/freight-alerts', READ)
        + _route(r'^/logistics/freight-alerts/\d+/dismiss/$', 'POST')
        + _collection('logistics/providers')
        + _collection('logistics/deliveries')
        + _collection('logistics/freight-rates', READ)
    ),
    SUPPLIER: (
        PRODUCTS_READ
        + _collection('inventory/suppliers')
        + _collection('logistics/suppliers')
        + _collection('logistics/alternative-suppliers', READ)
        + _collection('pricing/invoices')
        + _route(r'^/pricing/invoices/\d+/file/$', 'GET')
        + _collection('pricing/invoice-items')
    ),
    STOCK: (
        _collection('inventory/products')
        + _route(r'^/inventory/products/\d+/stock-projection/$', 'GET')
        + _collection('inventory/categories', READ)
        + _collection('inventory/batches')
        + _collection('inventory/movements', {'GET', 'POST'})
        + _collection('inventory/orders')
        + STOCK_ALERTS
        + _collection('pricing/price-changes', READ)
    ),
}


def role_may(role, method, path):
    routes = COMMON + ROLE_ROUTES.get(role, [])
    return any(pattern.match(path) and method in methods for pattern, methods in routes)
