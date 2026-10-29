# sync/test_inbound.py

import json
import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory
from inventory.models import StockMovement
from inventory.projections import average_daily_demand
from sync.models import InboundEvent, StorefrontConnection
from sync.signing import sign

SECRET = 'test-webhook-secret'


class OrderPaidInboundTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.connection = StorefrontConnection.objects.create(company=self.company, webhook_secret=SECRET)
        self.external_id = str(uuid.uuid4())
        self.product = ProductFactory(company=self.company, external_id=self.external_id, stock_quantity=10)
        self.batch = StockBatchFactory(product=self.product, quantity=10)
        self.url = reverse('storefront-inbound', args=[self.connection.pk])

    def envelope(self, items, event_type='order.paid', event_id=None):
        return {
            'event_id': event_id or str(uuid.uuid4()),
            'event_type': event_type,
            'occurred_at': '2026-10-16T10:00:00Z',
            'data': {'order_id': str(uuid.uuid4()), 'items': items},
        }

    def post(self, payload, secret=SECRET, url=None):
        body = json.dumps(payload).encode()
        return self.client.generic(
            'POST', url or self.url, body, content_type='application/json',
            HTTP_X_INTEGRATION_SIGNATURE=sign(secret, body),
        )

    def item(self, quantity, product_id=None):
        return {'product_id': product_id or self.external_id, 'quantity': quantity, 'unit_price': '2.50'}

    def test_order_paid_creates_outbound_movements(self):
        before = average_daily_demand(self.product)
        response = self.post(self.envelope([self.item(3)]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'applied')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)
        self.assertGreater(average_daily_demand(self.product), before)
        event = InboundEvent.objects.get()
        self.assertEqual((event.status, event.detail), ('applied', []))

    def test_duplicate_event_is_ignored(self):
        payload = self.envelope([self.item(2)])
        self.post(payload)
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'duplicate')
        self.assertEqual(StockMovement.objects.filter(movement_type='outbound').count(), 1)
        self.assertEqual(InboundEvent.objects.count(), 1)

    def test_unknown_product_recorded_not_retried(self):
        unknown = str(uuid.uuid4())
        response = self.post(self.envelope([self.item(1, unknown), self.item(1)]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'unmatched')
        event = InboundEvent.objects.get()
        self.assertEqual(event.status, 'unmatched')
        self.assertEqual(event.detail, [{'product_id': unknown, 'requested': 1, 'taken': 0, 'reason': 'unknown_product'}])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 9)  # the known item is still applied

    def test_short_stock_takes_what_exists_and_records_shortfall(self):
        response = self.post(self.envelope([self.item(15)]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'partial')
        event = InboundEvent.objects.get()
        self.assertEqual(event.detail, [{'product_id': self.external_id, 'requested': 15, 'taken': 10, 'reason': 'short_stock'}])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 0)

    def test_bad_signature_401_no_side_effects(self):
        bad = self.post(self.envelope([self.item(1)]), secret='wrong')
        missing = self.client.post(self.url, self.envelope([self.item(1)]), format='json')
        inactive = StorefrontConnection.objects.create(company=CompanyFactory(), webhook_secret=SECRET, is_active=False)
        off = self.post(self.envelope([self.item(1)]), url=reverse('storefront-inbound', args=[inactive.pk]))
        nowhere = self.post(self.envelope([self.item(1)]), url=reverse('storefront-inbound', args=[999999]))
        for response in (bad, missing, off, nowhere):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.content, bad.content)
        self.assertFalse(InboundEvent.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_other_company_product_not_touched(self):
        other = ProductFactory(company=CompanyFactory(), external_id=self.external_id, stock_quantity=10)
        other_batch = StockBatchFactory(product=other, quantity=10)
        self.post(self.envelope([self.item(4)]))
        other_batch.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((other_batch.quantity, other.stock_quantity), (10, 10))

    def test_unknown_event_type_stored_unmatched(self):
        response = self.post(self.envelope([], event_type='order.refunded'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(InboundEvent.objects.get().status, 'unmatched')
        self.assertFalse(StockMovement.objects.exists())

    def test_malformed_items_are_recorded_not_applied(self):
        response = self.post(self.envelope([{'product_id': self.external_id, 'quantity': 0}, 'junk']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(InboundEvent.objects.get().status, 'unmatched')
        self.assertFalse(StockMovement.objects.exists())

    def test_invalid_envelope_400(self):
        response = self.post({'event_type': 'order.paid'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(InboundEvent.objects.exists())
        self.assertEqual(Decimal(self.product.stock_quantity), Decimal(10))


class OrderPaidLocationTests(TestCase):
    """G09: web orders take stock from online-sellable locations only, and send it to CUSTOMER."""

    setUp = OrderPaidInboundTests.setUp
    envelope = OrderPaidInboundTests.envelope
    post = OrderPaidInboundTests.post
    item = OrderPaidInboundTests.item

    def test_order_takes_from_online_sellable_locations_only(self):
        from inventory.models import Location, StockBatch, StockMovement

        StockBatch.objects.filter(pk=self.batch.pk).update(quantity=0)
        store = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse', type='warehouse')
        hidden = StockBatchFactory(product=self.product, quantity=10, location=store, batch_number='WH-LOT')
        self.post(self.envelope([self.item(2)]))
        hidden.refresh_from_db()
        self.assertEqual(hidden.quantity, 10)  # the warehouse is not online-sellable
        self.assertFalse(StockMovement.objects.filter(batch=hidden).exists())

    def test_order_movement_goes_to_customer(self):
        from inventory.models import StockMovement

        self.post(self.envelope([self.item(2)]))
        movement = StockMovement.objects.get()
        self.assertEqual((movement.source_location, movement.destination_location.type),
                         (self.batch.location, 'customer'))
