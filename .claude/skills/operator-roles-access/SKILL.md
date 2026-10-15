---
name: operator-roles-access
description: Use when adding an endpoint operators should reach, adding or changing an operator role (till, demand, freight, supplier, stock), or touching 8-digit code login.
---

# Operator roles and code login

Operators (`CustomUser.role` of `till`, `demand`, `freight`, `supplier`, `stock`) sign in with an 8-digit code. Staff and regular users have no role and are not restricted by this layer.

## Access is default-deny
`authentication/role_access.py` holds a per-role allow-list of `(compiled regex, methods)`, checked on every request an operator makes. **A new endpoint is closed to operators until listed.**
- Helpers: `_collection('inventory/products', READ)` covers `/prefix/` and `/prefix/<id>/`; `_route(regex, 'GET', 'POST')` covers one path.
- Give each role only what its screens need (till: sessions/sales/products read; stock: products, batches, movements, orders, price-changes read; etc.).
- Tenancy and each viewset's own permissions still apply on top.

## Codes
- 8 digits, server-generated, stored only as an HMAC hash (`tenants/operator_codes.py`), shown to the admin **once**. Never log or return a stored code; to "reset", generate a new one.
- Login is throttled (5/min per client). The code is weak against distributed brute force; binding to a device/till is the suggested hardening.
- Role constants are in `tenants/roles.py`. Admins manage operators through `tenants` endpoints (and the frontend Team page).

## Adding or changing a role
1. Add the constant in `tenants/roles.py` and a migration for `choices` if the field has them.
2. Add its entry in `ROLE_ROUTES`.
3. Mirror it in the frontend `fe/src/roles.js` (`ROLES`, `ROLE_ITEMS`, `ROLE_HOME`). The frontend only decides what to show; the backend is the real gate.
4. Test in `tenants/test_operators.py`: allowed route works, a route outside the list gets 403, other company's data is hidden.
