# inventory/locations.py

"""The locations every company and branch starts with (G08)."""

from django.db import transaction

from tenants.models import Branch

from .models import Location

VIRTUAL_DEFAULTS = [
    (Location.IN_TRANSIT, 'IN-TRANSIT', 'In transit'),
    (Location.SUPPLIER, 'SUPPLIER', 'Suppliers'),
    (Location.CUSTOMER, 'CUSTOMER', 'Customers'),
    (Location.WASTE, 'WASTE', 'Waste'),
    (Location.ADJUSTMENT, 'ADJUSTMENT', 'Stock adjustments'),
]


def shop_floor_code(branch):
    """``SHOP-<branch id>``, or ``SHOP-<id>-2``, ``-3``... if that code is already taken in the company."""
    base, n = f'SHOP-{branch.pk}', 1
    code = base
    while Location.objects.filter(company_id=branch.company_id, code=code).exists():
        n += 1
        code = f'{base}-{n}'
    return code


@transaction.atomic
def ensure_default_locations(company, branches=None):
    """
    Give ``company`` its five virtual locations and a sellable shop floor to each of ``branches`` (default: all its
    branches). Only adds what is missing (a branch with any shop floor, whatever its code, keeps it). Returns how
    many locations were created.
    """
    created = 0
    have = set(Location.objects.filter(company=company, type__in=Location.VIRTUAL_TYPES).values_list('type', flat=True))
    for type_, code, name in VIRTUAL_DEFAULTS:
        if type_ not in have:
            Location.objects.create(company=company, type=type_, code=code, name=name)
            created += 1
    with_floor = set(
        Location.objects.filter(company=company, type=Location.SHOP_FLOOR, branch__isnull=False)
        .values_list('branch_id', flat=True)
    )
    candidates = Branch.objects.filter(company=company) if branches is None else branches
    for branch in candidates:
        if branch.pk in with_floor:
            continue
        Location.objects.create(
            company=company, branch=branch, type=Location.SHOP_FLOOR, code=shop_floor_code(branch),
            name=f'{branch.name} shop floor', sellable=True, online_sellable=True,
        )
        created += 1
    return created
