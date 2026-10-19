# sync/test_stock_push.py

import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory
from inventory.ledger import move, take_fefo
from inventory.models import StockBatch
from sync.models import OutboundEvent, StockSyncState, StorefrontConnection
from sync.stock import online_available
from sync.tasks import push_storefront_stock

BASE = 'https://m18.example.com/'


class StockPushTestCase(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url=BASE, webhook_secret='s')
        self.enterContext(mock.patch('sync.outbox.deliver_outbound_event.apply_async'))
        self.today = timezone.localdate()

    def linked(self, **extra):
        return ProductFactory(company=self.company, external_id=str(uuid.uuid4()), stock_quantity=0, **extra)

    def batch(self, product, quantity, days=30):
        return StockBatchFactory(product=product, quantity=quantity, expiration_date=self.today + timedelta(days=days))

    def pending(self):
        return set(StockSyncState.objects.filter(pending=True).values_list('product_id', flat=True))


class MarkingTests(StockPushTestCase):
    def test_batch_and_movement_and_product_edits_mark_the_product(self):
        product = self.linked()
        batch = self.batch(product, 5)
        self.assertEqual(self.pending(), {product.pk})

        StockSyncState.objects.update(pending=False)
        with transaction.atomic():
            move(StockBatch.objects.select_for_update().get(pk=batch.pk), 2, 'outbound')
        self.assertEqual(self.pending(), {product.pk})

        StockSyncState.objects.update(pending=False)
        product.online_buffer = 2
        product.save()
        self.assertEqual(self.pending(), {product.pk})

    def test_sales_mark_through_the_ledger(self):
        product = self.linked()
        self.batch(product, 5)
        StockSyncState.objects.update(pending=False)
        with transaction.atomic():
            take_fefo(product, 1)
        self.assertEqual(self.pending(), {product.pk})

    def test_deleting_a_batch_marks_on_commit(self):
        product = self.linked()
        batch = self.batch(product, 5)
        StockSyncState.objects.update(pending=False)
        with self.captureOnCommitCallbacks(execute=True):
            batch.delete()
        self.assertEqual(self.pending(), {product.pk})

    def test_deleting_a_product_does_not_fail(self):
        product = self.linked()
        self.batch(product, 5)
        with self.captureOnCommitCallbacks(execute=True):
            product.delete()
        self.assertFalse(StockSyncState.objects.exists())

    def test_unlinked_or_unconnected_products_are_not_marked(self):
        self.batch(ProductFactory(company=self.company, external_id=''), 5)
        other = ProductFactory(company=CompanyFactory(), external_id=str(uuid.uuid4()))
        self.batch(other, 5)
        StorefrontConnection.objects.create(company=CompanyFactory(), base_url='', webhook_secret='x')
        self.connection.is_active = False
        self.connection.save()
        self.batch(self.linked(), 5)
        self.assertEqual(self.pending(), set())


class AvailableTests(StockPushTestCase):
    def test_in_date_stock_minus_buffer_never_below_zero(self):
        product = self.linked(online_buffer=3)
        self.batch(product, 4)
        self.batch(product, 6, days=1)
        self.batch(product, 50, days=-1)  # expired
        self.batch(product, 0)
        self.assertEqual(online_available([product])[product.pk], 7)
        product.online_buffer = 40
        self.assertEqual(online_available([product])[product.pk], 0)
        empty = self.linked()
        self.assertEqual(online_available([empty])[empty.pk], 0)


class SweepTests(StockPushTestCase):
    def test_sweep_sends_one_versioned_snapshot_and_clears_pending(self):
        a, b = self.linked(online_buffer=1), self.linked()
        self.batch(a, 5)
        self.batch(b, 2)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(push_storefront_stock(), 2)
        event = OutboundEvent.objects.get()
        self.assertEqual((event.event_type, event.connection), ('stock.changed', self.connection))
        self.assertEqual(
            sorted(event.payload['data']['items'], key=lambda i: i['online_available']),
            [{'product_id': b.external_id, 'online_available': 2, 'version': 1},
             {'product_id': a.external_id, 'online_available': 4, 'version': 1}],
        )
        self.assertEqual(self.pending(), set())
        self.assertEqual(push_storefront_stock(), 0)  # nothing new: nothing sent
        self.assertEqual(OutboundEvent.objects.count(), 1)

        self.batch(a, 1)
        push_storefront_stock()
        latest = OutboundEvent.objects.latest('pk').payload['data']['items']
        self.assertEqual(latest, [{'product_id': a.external_id, 'online_available': 5, 'version': 2}])
        self.assertEqual(StockSyncState.objects.get(product=a).last_sent_available, 5)

    def test_sweep_batches_large_changes(self):
        for _ in range(3):
            self.batch(self.linked(), 1)
        with mock.patch('sync.tasks.STOCK_BATCH_SIZE', 2):
            self.assertEqual(push_storefront_stock(), 3)
        sizes = sorted(len(e.payload['data']['items']) for e in OutboundEvent.objects.all())
        self.assertEqual(sizes, [1, 2])

    def test_unlinked_after_marking_is_cleared_without_sending(self):
        product = self.linked()
        self.batch(product, 1)
        product.external_id = ''
        product.save()
        self.assertEqual(push_storefront_stock(), 0)
        self.assertFalse(OutboundEvent.objects.exists())
        self.assertEqual(self.pending(), set())

    def test_inactive_connection_is_not_swept(self):
        self.batch(self.linked(), 1)
        StorefrontConnection.objects.filter(pk=self.connection.pk).update(is_active=False)
        self.assertEqual(push_storefront_stock(), 0)
        self.assertEqual(len(self.pending()), 1)

    def test_command_marks_every_linked_product(self):
        a, b = self.linked(), self.linked()
        ProductFactory(company=self.company, external_id='')
        StockSyncState.objects.all().delete()
        out = StringIO()
        call_command('push_storefront_stock', stdout=out)
        self.assertEqual(self.pending(), {a.pk, b.pk})
        self.assertIn('2', out.getvalue())


class ProductApiTests(StockPushTestCase):
    def test_online_buffer_is_editable_through_the_api(self):
        from django.urls import reverse
        from rest_framework.test import APIClient

        from common.tests.factories import UserFactory

        product = self.linked()
        client = APIClient()
        client.force_authenticate(UserFactory(company=self.company, is_staff=True))
        response = client.patch(reverse('product-detail', args=[product.pk]), {'online_buffer': 4}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        product.refresh_from_db()
        self.assertEqual(product.online_buffer, 4)


def m18_reply(status_code, items=None):
    response = mock.Mock(status_code=status_code, text='')
    response.json.return_value = {'items': items or []}
    return response


@mock.patch('sync.tasks.requests.post')
class ReconcileTests(StockPushTestCase):
    def test_reports_differences_and_queues_everything(self, post):
        from sync.models import StockReconciliation
        from sync.signing import verify
        from sync.tasks import reconcile_storefront_stock

        same, drifted, missing = self.linked(), self.linked(), self.linked()
        self.batch(same, 3)
        self.batch(drifted, 5)
        stray = str(uuid.uuid4())
        post.return_value = m18_reply(200, [
            {'product_id': same.external_id, 'iq_managed': True, 'iq_available': 3, 'stock_quantity': 3},
            {'product_id': drifted.external_id, 'iq_managed': True, 'iq_available': 9, 'stock_quantity': 9},
            {'product_id': stray, 'iq_managed': True, 'iq_available': 1, 'stock_quantity': 1},
            {'product_id': str(uuid.uuid4()), 'iq_managed': False, 'iq_available': None, 'stock_quantity': 4},
        ])
        StockSyncState.objects.update(pending=False)
        with self.assertLogs('sync.tasks', 'WARNING'):
            reconcile_storefront_stock()

        (url,), kwargs = post.call_args
        self.assertEqual(url, BASE + 'integration/stock/')
        self.assertTrue(verify('s', kwargs['data'], kwargs['headers']['X-Integration-Signature']))
        run = StockReconciliation.objects.get()
        self.assertEqual((run.connection, run.checked, run.error), (self.connection, 3, ''))
        self.assertEqual(sorted(run.mismatches, key=lambda m: m['kind']), [
            {'product_id': drifted.external_id, 'kind': 'different', 'iq': 5, 'm18': 9},
            {'product_id': missing.external_id, 'kind': 'missing_in_m18', 'iq': 0, 'm18': None},
            {'product_id': stray, 'kind': 'not_linked_in_iq', 'iq': None, 'm18': 1},
        ])
        self.assertEqual(self.pending(), {same.pk, drifted.pk, missing.pk})  # resent by the next sweep

    def test_m18_unreachable_records_the_error_and_still_queues(self, post):
        import requests

        from sync.models import StockReconciliation
        from sync.tasks import reconcile_storefront_stock

        product = self.linked()
        StockSyncState.objects.update(pending=False)
        for failure in (requests.ConnectionError(), None):
            post.side_effect, post.return_value = failure, m18_reply(503)
            reconcile_storefront_stock()
        errors = list(StockReconciliation.objects.order_by('pk').values_list('error', flat=True))
        self.assertEqual(errors, ['ConnectionError', 'HTTP 503'])
        self.assertEqual(self.pending(), {product.pk})


class PricePushTests(StockPushTestCase):
    def items(self):
        return OutboundEvent.objects.latest('pk').payload['data']['items']

    def test_price_travels_only_when_switched_on(self):
        product = self.linked(price='4.5')
        self.batch(product, 2)
        push_storefront_stock()
        self.assertNotIn('price', self.items()[0])

        StorefrontConnection.objects.filter(pk=self.connection.pk).update(push_prices=True)
        product.refresh_from_db()
        product.save()
        push_storefront_stock()
        self.assertEqual(self.items(), [{'product_id': product.external_id, 'online_available': 2,
                                         'version': 2, 'price': '4.50'}])

    def test_an_approved_change_goes_out_on_the_next_sweep(self):
        from decimal import Decimal

        from rest_framework.test import APIClient

        from common.tests.factories import UserFactory
        from pricing.models import PriceChangeLog

        StorefrontConnection.objects.filter(pk=self.connection.pk).update(push_prices=True)
        product = self.linked(price=Decimal('3.00'))
        push_storefront_stock()
        log = PriceChangeLog.objects.create(product=product, old_price=Decimal('3.00'), new_price=Decimal('2.49'))
        client = APIClient()
        client.force_authenticate(UserFactory(company=self.company, is_staff=True))
        response = client.post(f'/pricing/price-changes/{log.pk}/approve/')
        self.assertEqual(response.status_code, 200, response.content)
        push_storefront_stock()
        self.assertEqual(self.items()[0]['price'], '2.49')
        self.assertEqual(self.items()[0]['version'], 2)

    def test_switching_on_in_the_admin_queues_every_linked_product(self):
        from django.contrib.admin.sites import site

        a, b = self.linked(), self.linked()
        StockSyncState.objects.update(pending=False)
        admin = site._registry[StorefrontConnection]
        form = mock.Mock(changed_data=['push_prices'])
        self.connection.push_prices = True
        with self.captureOnCommitCallbacks(execute=True):
            admin.save_model(mock.Mock(), self.connection, form, True)
        self.assertEqual(self.pending(), {a.pk, b.pk})


@mock.patch('sync.tasks.requests.post')
class PriceReconcileTests(StockPushTestCase):
    def test_price_differences_are_reported(self, post):
        from sync.models import StockReconciliation
        from sync.tasks import reconcile_storefront_stock

        same, drifted, old_m18 = self.linked(price='2.00'), self.linked(price='3.00'), self.linked(price='1.00')
        post.return_value = m18_reply(200, [
            {'product_id': same.external_id, 'iq_available': 0, 'price': '2.00'},
            {'product_id': drifted.external_id, 'iq_available': 0, 'price': '3.49'},
            {'product_id': old_m18.external_id, 'iq_available': 0},  # an m18 that does not send prices
        ])
        with self.assertLogs('sync.tasks', 'WARNING'):
            reconcile_storefront_stock()
        self.assertEqual(StockReconciliation.objects.get().mismatches, [
            {'product_id': drifted.external_id, 'kind': 'price_different', 'iq': '3.00', 'm18': '3.49'},
        ])
