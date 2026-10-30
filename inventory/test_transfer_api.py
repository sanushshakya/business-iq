# inventory/test_transfer_api.py

"""G11: the transfers API — tenancy, roles, and dispatch/receive/cancel over HTTP."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Location, StockBatch, Transfer
from tenants.models import Branch


class TransferApiSetUp(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.wh = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse', type='warehouse')
        self.product = ProductFactory(company=self.company, stock_quantity=10, name='Fake rice')
        StockBatchFactory(product=self.product, quantity=10, location=self.wh, batch_number='LOT-1')
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def url(self, pk=None, action=None):
        if pk is None:
            return reverse('transfer-list')
        return reverse(f'transfer-{action}' if action else 'transfer-detail', args=[pk])

    def body(self, **fields):
        return {'source_location': self.wh.pk, 'destination_location': self.shop.pk,
                'lines': [{'product': self.product.pk, 'quantity': 6}], **fields}

    def create(self, api=None, **fields):
        return (api or self.api).post(self.url(), self.body(**fields), format='json')

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api


class TransferApiTests(TransferApiSetUp):
    def test_create_dispatch_receive_happy_path(self):
        response = self.create(note='Weekly top-up')
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data['reference'], data['status'], data['note']), ('T-000001', 'draft', 'Weekly top-up'))
        self.assertEqual(data['lines'][0]['quantity'], 6)

        response = self.api.post(self.url(data['id'], 'dispatch'))
        self.assertEqual((response.status_code, response.json()['status']), (200, 'dispatched'), response.content)

        line = response.json()['lines'][0]['id']
        response = self.api.post(self.url(data['id'], 'receive'),
                                 {'lines': [{'line': line, 'received_quantity': 5, 'reason': 'One damaged'}]},
                                 format='json')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['status'], 'received')
        self.assertEqual((data['lines'][0]['received_quantity'], data['lines'][0]['difference_reason']),
                         (5, 'One damaged'))
        self.assertEqual(StockBatch.objects.get(location=self.shop).quantity, 5)

    def test_list_filters_by_status(self):
        self.create()
        cancelled = self.create().json()['id']
        self.api.post(self.url(cancelled, 'cancel'))
        response = self.api.get(self.url(), {'status': 'cancelled'})
        self.assertEqual([row['id'] for row in response.json()['results']], [cancelled])

    def test_other_company_transfer_404(self):
        other = CompanyFactory()
        other_api = APIClient()
        other_api.force_authenticate(UserFactory(company=other, is_staff=True))
        pk = self.create().json()['id']
        self.assertEqual(other_api.get(self.url(pk)).status_code, 404)
        self.assertEqual(other_api.post(self.url(pk, 'dispatch')).status_code, 404)
        self.assertEqual(other_api.post(self.url(pk, 'cancel')).status_code, 404)
        self.assertEqual(other_api.get(self.url()).json()['count'], 0)
        self.assertEqual(Transfer.objects.get(pk=pk).status, 'draft')

    def test_company_in_body_ignored(self):
        other = CompanyFactory()
        response = self.create(company=other.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Transfer.objects.get().company, self.company)

    def test_foreign_location_or_product_400(self):
        other = CompanyFactory()
        foreign_location = Location.objects.create(company=other, code='WH-X', name='Fake other', type='warehouse')
        foreign_product = ProductFactory(company=other)
        self.assertEqual(self.create(destination_location=foreign_location.pk).status_code, 400)
        self.assertEqual(self.create(lines=[{'product': foreign_product.pk, 'quantity': 1}]).status_code, 400)
        pk = self.create().json()['id']
        response = self.api.patch(self.url(pk), {'destination_location': foreign_location.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Transfer.objects.get(pk=pk).destination_location, self.shop)

    def test_virtual_or_same_location_400(self):
        transit = Location.objects.get(company=self.company, type='in_transit')
        self.assertEqual(self.create(destination_location=transit.pk).status_code, 400)
        response = self.create(destination_location=self.wh.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn('destination_location', response.json())
        self.assertEqual(self.create(lines=[]).status_code, 400)
        self.assertEqual(self.create(lines=[{'product': self.product.pk, 'quantity': 0}]).status_code, 400)

    def test_patch_draft_lines(self):
        pk = self.create().json()['id']
        response = self.api.patch(self.url(pk), {'lines': [{'product': self.product.pk, 'quantity': 2}]},
                                  format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([line['quantity'] for line in response.json()['lines']], [2])
        response = self.api.patch(self.url(pk), {'note': 'Only the note'}, format='json')
        self.assertEqual((response.status_code, len(response.json()['lines'])), (200, 1))

    def test_stock_operator_allowed_till_operator_403(self):
        stock = self.operator('stock')
        response = self.create(api=stock)
        self.assertEqual(response.status_code, 201, response.content)
        pk = response.json()['id']
        self.assertEqual(stock.get(self.url(pk)).status_code, 200)
        self.assertEqual(stock.post(self.url(pk, 'dispatch')).status_code, 200)

        till = self.operator('till')
        self.assertEqual(till.get(self.url()).status_code, 403)
        self.assertEqual(self.create(api=till).status_code, 403)
        self.assertEqual(till.post(self.url(pk, 'receive'), {'lines': []}, format='json').status_code, 403)

    def test_delete_not_allowed(self):
        pk = self.create().json()['id']
        self.assertEqual(self.api.delete(self.url(pk)).status_code, 405)
        self.assertTrue(Transfer.objects.filter(pk=pk).exists())

    def test_edit_after_dispatch_400(self):
        pk = self.create().json()['id']
        self.api.post(self.url(pk, 'dispatch'))
        response = self.api.patch(self.url(pk), {'note': 'Too late'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api.post(self.url(pk, 'cancel')).status_code, 400)
        self.assertEqual(self.api.post(self.url(pk, 'dispatch')).status_code, 400)

    def test_short_dispatch_400_message(self):
        pk = self.create(lines=[{'product': self.product.pk, 'quantity': 11}]).json()['id']
        response = self.api.post(self.url(pk, 'dispatch'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['lines'], ['Fake rice: only 10 available at WH-1.'])

    def test_receive_validation_400(self):
        pk = self.create().json()['id']
        line = self.api.post(self.url(pk, 'dispatch')).json()['lines'][0]['id']
        for lines in ([{'line': line, 'received_quantity': 4}], [{'line': line, 'received_quantity': -1}], []):
            with self.subTest(lines=lines):
                response = self.api.post(self.url(pk, 'receive'), {'lines': lines}, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Transfer.objects.get(pk=pk).status, 'dispatched')


class TransitStockGuardTests(TransferApiSetUp):
    """Stock in transit belongs to its transfers: the batch and movement APIs can't change it."""

    def dispatched_transit_batch(self):
        pk = self.create().json()['id']
        self.api.post(self.url(pk, 'dispatch'))
        return StockBatch.objects.get(location__type='in_transit')

    def test_movement_from_a_transit_batch_400(self):
        held = self.dispatched_transit_batch()
        response = self.api.post(reverse('stockmovement-list'),
                                 {'batch': held.pk, 'quantity': 6, 'movement_type': 'outbound'}, format='json')
        self.assertEqual(response.status_code, 400)
        held.refresh_from_db()
        self.assertEqual(held.quantity, 6)

    def test_editing_a_transit_batch_400(self):
        held = self.dispatched_transit_batch()
        response = self.api.patch(reverse('stockbatch-detail', args=[held.pk]), {'quantity': 0}, format='json')
        self.assertEqual(response.status_code, 400)
        held.refresh_from_db()
        self.assertEqual(held.quantity, 6)

    def test_status_query_on_an_action_is_ignored(self):
        pk = self.create().json()['id']
        response = self.api.post(self.url(pk, 'dispatch') + '?status=draft')
        self.assertEqual((response.status_code, response.json()['status']), (200, 'dispatched'))

    def test_transfer_admins_are_view_only(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from inventory.models import TransferLine, TransferPick

        request = RequestFactory().get('/')
        request.user = UserFactory(company=self.company, is_staff=True, is_superuser=True)
        for model in (Transfer, TransferLine, TransferPick):
            model_admin = admin.site._registry[model]
            with self.subTest(model=model.__name__):
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))

    def test_superuser_without_company_400(self):
        api = APIClient()
        api.force_authenticate(UserFactory(company=None, is_staff=True, is_superuser=True))
        response = self.create(api=api)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Transfer.objects.count(), 0)
