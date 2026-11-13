"""G59 test helpers: a company with two branches, a warehouse, and stock moved only through the ledger at chosen
times, so an independent replay of ``StockMovement`` rows can check the dashboard."""

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.test import TestCase

from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.ledger import move, virtual_location
from inventory.models import Location, StockBatch, StockMovement
from tenants.models import Branch
from vat.services import UK

D = Decimal
MONDAY = date(2026, 3, 2)  # weeks: 2026-02-23 (previous) and 2026-03-02 (this)


def at(day, hour=12):
    return datetime.combine(day, time(hour), UK)


@contextmanager
def clock(moment):
    """Everything stamped inside (``auto_now_add``, ``timezone.now``) happens at ``moment``."""
    with mock.patch('django.utils.timezone.now', return_value=moment):
        yield


class LedgerCase(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.north = Branch.objects.create(company=self.company, name='Fake North', address='-')
        self.south = Branch.objects.create(company=self.company, name='Fake South', address='-')
        self.north_shop = Location.objects.get(company=self.company, branch=self.north, type=Location.SHOP_FLOOR)
        self.south_shop = Location.objects.get(company=self.company, branch=self.south, type=Location.SHOP_FLOOR)
        self.warehouse = Location.objects.create(company=self.company, code='WH-FAKE', name='Fake Warehouse',
                                                 type=Location.WAREHOUSE)
        self.n = 0

    def virtual(self, kind):
        return virtual_location(self.company.pk, kind)

    def receive(self, product, location, quantity, *, cost, when, kg=None):
        """A new lot received from SUPPLIER at ``when`` (created empty, filled by an inbound movement)."""
        self.n += 1
        with clock(when), transaction.atomic():
            batch = StockBatch.objects.create(product=product, batch_number=f'FAKE-{self.n}', quantity=0,
                                              location=location, expiration_date=when.date() + timedelta(days=60),
                                              unit_cost=None if cost is None else D(cost))
            batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
            move(batch, quantity, 'inbound', counterpart=self.virtual(Location.SUPPLIER),
                 weight_kg=None if kg is None else D(kg))
        return batch

    def out(self, batch, quantity, to, *, when, kg=None):
        with clock(when), transaction.atomic():
            locked = StockBatch.objects.select_for_update().get(pk=batch.pk)
            return move(locked, quantity, 'outbound', counterpart=self.virtual(to),
                        weight_kg=None if kg is None else D(kg))

    def product(self, name, **fields):
        return ProductFactory(company=self.company, name=name, price=D('2.00'), stock_quantity=0, **fields)


def replay_value(company, until):
    """Independent check: every movement up to ``until`` replayed forward from empty, × each batch's cost, by location
    (real ones and In transit)."""
    held = {}
    for movement in StockMovement.objects.filter(batch__product__company=company, date_time__lt=until) \
            .select_related('batch__product', 'batch__location'):
        sign = 1 if movement.movement_type == 'inbound' else -1
        units, kg = held.get(movement.batch_id, (0, D('0')))
        held[movement.batch_id] = (units + sign * movement.quantity, kg + sign * (movement.weight_kg or 0))
    values = {}
    for batch in StockBatch.objects.filter(pk__in=held).select_related('product', 'location'):
        if batch.location.type in ('supplier', 'customer', 'waste', 'adjustment'):
            continue
        units, kg = held[batch.pk]
        amount = kg if batch.product.is_weighed else units
        if not units and not kg:
            continue
        value = D('0') if batch.unit_cost is None else (amount * batch.unit_cost).quantize(D('0.01'))
        values[batch.location.code] = values.get(batch.location.code, D('0')) + value  # uncosted stock: 0, listed
    return values
