# inventory/test_requisitions.py

"""G12: store requisitions, fulfilled by the warehouse as transfers (in full or in parts)."""

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory import requisitions, transfers
from inventory.models import Location, Requisition, StockBatch, Transfer
from tenants.models import Branch


class RequisitionSetUp(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.user = UserFactory(company=self.company, is_staff=True)
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.wh = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse', type='warehouse')
        self.rice = ProductFactory(company=self.company, stock_quantity=20, name='Fake rice')
        self.oil = ProductFactory(company=self.company, stock_quantity=20, name='Fake oil')
        for product in (self.rice, self.oil):
            StockBatchFactory(product=product, quantity=20, location=self.wh, batch_number='LOT-1')

    def raise_requisition(self, lines=None, location=None):
        return requisitions.create_requisition(
            company=self.company, user=self.user, location=location or self.shop,
            lines=lines or [(self.rice, 10), (self.oil, 4)],
        )

    def lines(self, requisition):
        return {line.product.name: line for line in requisition.lines.select_related('product')}

    def at_shop(self, product):
        return StockBatch.objects.filter(location=self.shop, product=product).aggregate(q=Sum('quantity'))['q'] or 0


class RequisitionFlowTests(RequisitionSetUp):
    def test_requisition_to_transfer_to_received(self):
        requisition = self.raise_requisition()
        self.assertEqual((requisition.reference, requisition.status), ('R-000001', Requisition.OPEN))
        rice, oil = self.lines(requisition)['Fake rice'], self.lines(requisition)['Fake oil']

        transfer = requisitions.fulfil(requisition, self.user, source=self.wh,
                                       quantities={rice.pk: 10, oil.pk: 4})
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.FULFILLED)
        self.assertEqual((transfer.status, transfer.source_location, transfer.destination_location),
                         (Transfer.DRAFT, self.wh, self.shop))
        self.assertEqual(sorted((line.product.name, line.quantity, line.requisition_line_id)
                                for line in transfer.lines.select_related('product')),
                         [('Fake oil', 4, oil.pk), ('Fake rice', 10, rice.pk)])

        transfers.dispatch(transfer, self.user)
        transfer_lines = {line.product_id: line.pk for line in transfer.lines.all()}
        transfers.receive(transfer, self.user, [
            {'line': transfer_lines[self.rice.pk], 'received_quantity': 10, 'reason': ''},
            {'line': transfer_lines[self.oil.pk], 'received_quantity': 4, 'reason': ''},
        ])
        self.assertEqual((self.at_shop(self.rice), self.at_shop(self.oil)), (10, 4))
        self.assertEqual([t.reference for t in requisition.transfers()], ['T-000001'])

    def test_partial_fulfilment_leaves_the_remainder_open(self):
        requisition = self.raise_requisition()
        rice, oil = self.lines(requisition)['Fake rice'], self.lines(requisition)['Fake oil']

        requisitions.fulfil(requisition, self.user, source=self.wh, quantities={rice.pk: 6})
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.PARTLY_FULFILLED)
        rice, oil = self.lines(requisition)['Fake rice'], self.lines(requisition)['Fake oil']
        self.assertEqual((rice.fulfilled_quantity, rice.open_quantity, oil.open_quantity), (6, 4, 4))

        second = requisitions.fulfil(requisition, self.user, source=self.wh, quantities={rice.pk: 4, oil.pk: 4})
        requisition.refresh_from_db()
        self.assertEqual((requisition.status, second.number), (Requisition.FULFILLED, 2))

    def test_cannot_fulfil_more_than_is_open(self):
        requisition = self.raise_requisition()
        rice = self.lines(requisition)['Fake rice']
        requisitions.fulfil(requisition, self.user, source=self.wh, quantities={rice.pk: 7})
        with self.assertRaises(requisitions.RequisitionError):
            requisitions.fulfil(requisition, self.user, source=self.wh, quantities={rice.pk: 4})
        self.assertEqual(Transfer.objects.count(), 1)

    def test_fulfil_needs_something_and_its_own_lines(self):
        requisition = self.raise_requisition()
        other = self.raise_requisition([(self.rice, 1)])
        for quantities in ({}, {self.lines(requisition)['Fake rice'].pk: 0}, {other.lines.get().pk: 1}):
            with self.subTest(quantities=quantities), self.assertRaises(requisitions.RequisitionError):
                requisitions.fulfil(requisition, self.user, source=self.wh, quantities=quantities)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_cancelled_transfer_reopens_its_units(self):
        requisition = self.raise_requisition()
        rice = self.lines(requisition)['Fake rice']
        transfer = requisitions.fulfil(requisition, self.user, source=self.wh, quantities={rice.pk: 10})
        transfers.cancel(transfer, self.user)
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.OPEN)
        self.assertEqual(self.lines(requisition)['Fake rice'].open_quantity, 10)

    def test_short_receipt_does_not_reopen(self):
        requisition = self.raise_requisition([(self.rice, 10)])
        transfer = requisitions.fulfil(requisition, self.user, source=self.wh,
                                       quantities={requisition.lines.get().pk: 10})
        transfers.dispatch(transfer, self.user)
        transfers.receive(transfer, self.user, [{'line': transfer.lines.get().pk, 'received_quantity': 8,
                                                 'reason': 'Two damaged'}])
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.FULFILLED)

    def test_cancel_closes_the_remainder(self):
        requisition = self.raise_requisition()
        rice = self.lines(requisition)['Fake rice']
        transfer = requisitions.fulfil(requisition, self.user, source=self.wh, quantities={rice.pk: 3})
        requisitions.cancel(requisition, self.user)
        requisition.refresh_from_db()
        self.assertEqual((requisition.status, requisition.cancelled_by), (Requisition.CANCELLED, self.user))
        self.assertEqual(Transfer.objects.get(pk=transfer.pk).status, Transfer.DRAFT)  # made transfers stay
        with self.assertRaises(requisitions.RequisitionError):
            requisitions.fulfil(requisition, self.user, source=self.wh, quantities={rice.pk: 1})
        with self.assertRaises(requisitions.RequisitionError):
            requisitions.cancel(requisition, self.user)

    def test_fulfilled_requisition_cannot_be_cancelled(self):
        requisition = self.raise_requisition([(self.rice, 2)])
        requisitions.fulfil(requisition, self.user, source=self.wh, quantities={requisition.lines.get().pk: 2})
        with self.assertRaises(requisitions.RequisitionError):
            requisitions.cancel(requisition, self.user)

    def test_route_and_line_rules(self):
        transit = Location.objects.get(company=self.company, type='in_transit')
        other = CompanyFactory()
        with self.assertRaises(requisitions.RequisitionError):
            self.raise_requisition(location=transit)
        with self.assertRaises(requisitions.RequisitionError):
            self.raise_requisition(lines=[(ProductFactory(company=other), 1)])
        with self.assertRaises(requisitions.RequisitionError):
            self.raise_requisition(lines=[(self.rice, 1), (self.rice, 2)])
        requisition = self.raise_requisition()
        with self.assertRaises(requisitions.RequisitionError):  # from the store to itself
            requisitions.fulfil(requisition, self.user, source=self.shop,
                                quantities={self.lines(requisition)['Fake rice'].pk: 1})

    def test_transfer_from_a_requisition_keeps_its_lines(self):
        requisition = self.raise_requisition()
        transfer = requisitions.fulfil(requisition, self.user, source=self.wh,
                                       quantities={self.lines(requisition)['Fake rice'].pk: 5})
        with self.assertRaises(transfers.TransferError):
            transfers.replace_draft(transfer, source=self.wh, destination=self.shop, lines=[(self.rice, 9)])
        transfers.replace_draft(transfer, source=self.wh, destination=self.shop, lines=None, note='Van 2')
        self.assertEqual(transfer.lines.get().quantity, 5)

    def test_numbers_per_company(self):
        self.assertEqual([self.raise_requisition().number, self.raise_requisition().number], [1, 2])


class RequisitionApiTests(RequisitionSetUp):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def url(self, pk=None, action=None):
        if pk is None:
            return reverse('requisition-list')
        return reverse(f'requisition-{action}' if action else 'requisition-detail', args=[pk])

    def create(self, api=None, **fields):
        body = {'location': self.shop.pk, 'lines': [{'product': self.rice.pk, 'quantity': 10}], **fields}
        return (api or self.api).post(self.url(), body, format='json')

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def test_raise_fulfil_in_parts_over_http(self):
        response = self.create(note='For Saturday')
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data['reference'], data['status'], data['lines'][0]['open_quantity']), ('R-000001', 'open', 10))
        line = data['lines'][0]['id']

        response = self.api.post(self.url(data['id'], 'fulfil'),
                                 {'source_location': self.wh.pk, 'lines': [{'line': line, 'quantity': 6}]},
                                 format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['reference'], response.json()['status']), ('T-000001', 'draft'))

        data = self.api.get(self.url(data['id'])).json()
        self.assertEqual((data['status'], data['lines'][0]['open_quantity']), ('partly_fulfilled', 4))
        self.assertEqual(data['transfers'], [response.json()['id']])

        response = self.api.post(self.url(data['id'], 'fulfil'),
                                 {'source_location': self.wh.pk, 'lines': [{'line': line, 'quantity': 5}]},
                                 format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api.get(self.url(), {'status': 'partly_fulfilled'}).json()['count'], 1)

    def test_other_company_404_and_body_company_ignored(self):
        pk = self.create(company=CompanyFactory().pk).json()['id']
        self.assertEqual(Requisition.objects.get().company, self.company)
        other = APIClient()
        other.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True))
        self.assertEqual(other.get(self.url(pk)).status_code, 404)
        self.assertEqual(other.post(self.url(pk, 'cancel')).status_code, 404)
        self.assertEqual(other.get(self.url()).json()['count'], 0)

    def test_foreign_location_or_product_400(self):
        other = CompanyFactory()
        foreign = Location.objects.create(company=other, code='WH-X', name='Fake other', type='warehouse')
        self.assertEqual(self.create(location=foreign.pk).status_code, 400)
        self.assertEqual(self.create(lines=[{'product': ProductFactory(company=other).pk, 'quantity': 1}]).status_code, 400)
        pk = self.create().json()['id']
        line = Requisition.objects.get(pk=pk).lines.get().pk
        response = self.api.post(self.url(pk, 'fulfil'), {'source_location': foreign.pk,
                                                          'lines': [{'line': line, 'quantity': 1}]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_no_edit_or_delete(self):
        pk = self.create().json()['id']
        self.assertEqual(self.api.patch(self.url(pk), {'note': 'x'}, format='json').status_code, 405)
        self.assertEqual(self.api.delete(self.url(pk)).status_code, 405)

    def test_stock_operator_allowed_till_operator_403(self):
        stock = self.operator('stock')
        response = self.create(api=stock)
        self.assertEqual(response.status_code, 201, response.content)
        pk, line = response.json()['id'], response.json()['lines'][0]['id']
        response = stock.post(self.url(pk, 'fulfil'), {'source_location': self.wh.pk,
                                                        'lines': [{'line': line, 'quantity': 10}]}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        till = self.operator('till')
        self.assertEqual(till.get(self.url()).status_code, 403)
        self.assertEqual(self.create(api=till).status_code, 403)
        self.assertEqual(till.post(self.url(pk, 'cancel')).status_code, 403)

    def test_admin_is_view_only(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from inventory.models import RequisitionLine

        request = RequestFactory().get('/')
        request.user = UserFactory(company=self.company, is_staff=True, is_superuser=True)
        for model in (Requisition, RequisitionLine):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
