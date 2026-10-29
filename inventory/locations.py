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
    return f'SHOP-{branch.pk}'


@transaction.atomic
def ensure_default_locations(company):
    """
    Give ``company`` its five virtual locations and each of its branches a sellable shop floor. Only adds what is
    missing (a branch with any shop floor, whatever its code, keeps it). Returns how many locations were created.
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
    for branch in Branch.objects.filter(company=company).exclude(pk__in=with_floor):
        Location.objects.create(
            company=company, branch=branch, type=Location.SHOP_FLOOR, code=shop_floor_code(branch),
            name=f'{branch.name} shop floor', sellable=True, online_sellable=True,
        )
        created += 1
    return created
