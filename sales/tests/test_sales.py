from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Product, StockBatch, StockMovement
from sales.models import Sale, TillSession
from tenants.models import Branch, Till

_till_numbers = iter(range(1, 10_000))


def make_till(company, **extra):
    branch = Branch.objects.create(company=company, name='High Street', address='1 High Street')
    return Till.objects.create(branch=branch, number=next(_till_numbers), device_id=f'dev-{next(_till_numbers)}', **extra)


class SalesTestCase(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.cashier = UserFactory(company=self.company)
        self.manager = UserFactory(company=self.company, is_staff=True)
        self.till = make_till(self.company)
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def stocked(self, price='2.50', batches=((10, 30),), **extra):
        """A product with one batch per ``(quantity, days_to_expiry)``."""
        product = ProductFactory(company=self.company, price=Decimal(price), stock_quantity=0, **extra)
        for quantity, days in batches:
            StockBatchFactory(
                product=product, quantity=quantity, expiration_date=timezone.localdate() + timedelta(days=days)
            )
            Product.objects.filter(pk=product.pk).update(stock_quantity=sum(q for q, _ in batches))
        return product

    def open(self, float_='50.00', client=None, till=None):
        response = (client or self.client).post(
            reverse('tillsession-list'), {'till': (till or self.till).pk, 'opening_float': float_}
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def sell(self, session_id, items, method='cash', tendered=None, client=None, client_ref=None):
        body = {
            'session': session_id,
            'payment_method': method,
            'items': [{'product': p.pk, 'quantity': q} for p, q in items],
        }
        if tendered is not None:
            body['amount_tendered'] = tendered
        if client_ref is not None:
            body['client_ref'] = client_ref
        return (client or self.client).post(reverse('sale-list'), body, format='json')


class SessionTests(SalesTestCase):
    def test_open_session(self):
        session = self.open('75.00')
        self.assertTrue(session['is_open'])
        self.assertEqual(session['opening_float'], '75.00')
        self.assertEqual(session['summary']['expected_cash'], '75.00')
        self.assertEqual(session['till_number'], self.till.number)

    def test_a_till_has_one_open_session(self):
        self.open()
        response = self.client.post(reverse('tillsession-list'), {'till': self.till.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn('already has an open session', str(response.json()))

    def test_cannot_open_an_inactive_till(self):
        self.till.is_active = False
        self.till.save()
        self.assertEqual(self.client.post(reverse('tillsession-list'), {'till': self.till.pk}).status_code, 400)

    def test_cannot_open_another_companys_till(self):
        other = make_till(CompanyFactory())
        response = self.client.post(reverse('tillsession-list'), {'till': other.pk})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TillSession.objects.count(), 0)

    def test_negative_float_is_rejected(self):
        self.assertEqual(
            self.client.post(reverse('tillsession-list'), {'till': self.till.pk, 'opening_float': '-1'}).status_code,
            400,
        )

    def test_filter_by_till_and_status(self):
        session = self.open()
        self.client.post(reverse('tillsession-close', args=[session['id']]), {'counted_cash': '50.00'})
        second = self.open()
        url = reverse('tillsession-list')
        open_ids = [s['id'] for s in self.client.get(url, {'status': 'open'}).json()['results']]
        closed_ids = [s['id'] for s in self.client.get(url, {'status': 'closed'}).json()['results']]
        self.assertEqual(open_ids, [second['id']])
        self.assertEqual(closed_ids, [session['id']])
        self.assertEqual(self.client.get(url, {'till': self.till.pk}).json()['count'], 2)
        self.assertEqual(self.client.get(url, {'till': self.till.pk + 999}).json()['count'], 0)

    def test_close_records_expected_cash_and_variance(self):
        session = self.open('50.00')
        product = self.stocked(price='4.00', batches=((20, 30),))
        self.assertEqual(self.sell(session['id'], [(product, 2)], 'cash', '10.00').status_code, 201)  # 8.00 cash
        self.assertEqual(self.sell(session['id'], [(product, 1)], 'card').status_code, 201)  # 4.00 card
        response = self.client.post(
            reverse('tillsession-close', args=[session['id']]), {'counted_cash': '57.00', 'note': 'short a pound'}
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body['is_open'])
        self.assertEqual(body['expected_cash'], '58.00')
        self.assertEqual(body['counted_cash'], '57.00')
        self.assertEqual(body['summary']['variance'], '-1.00')
        self.assertEqual(body['summary']['cash_total'], '8.00')
        self.assertEqual(body['summary']['card_total'], '4.00')
        self.assertEqual(body['summary']['sales_total'], '12.00')
        self.assertEqual(body['note'], 'short a pound')

    def test_cannot_close_twice(self):
        session = self.open()
        url = reverse('tillsession-close', args=[session['id']])
        self.assertEqual(self.client.post(url, {'counted_cash': '50.00'}).status_code, 200)
        self.assertEqual(self.client.post(url, {'counted_cash': '50.00'}).status_code, 400)

    def test_only_the_opener_or_staff_can_close(self):
        session = self.open()
        url = reverse('tillsession-close', args=[session['id']])
        colleague = UserFactory(company=self.company)
        self.assertEqual(self.as_user(colleague).post(url, {'counted_cash': '50'}).status_code, 403)
        self.assertEqual(self.as_user(self.manager).post(url, {'counted_cash': '50'}).status_code, 200)

    def test_counted_cash_is_required(self):
        session = self.open()
        self.assertEqual(self.client.post(reverse('tillsession-close', args=[session['id']]), {}).status_code, 400)

    def test_sessions_are_scoped_to_the_company(self):
        session = self.open()
        outsider = self.as_user(UserFactory())
        self.assertEqual(outsider.get(reverse('tillsession-list')).json()['count'], 0)
        self.assertEqual(outsider.get(reverse('tillsession-detail', args=[session['id']])).status_code, 404)
        self.assertEqual(
            outsider.post(reverse('tillsession-close', args=[session['id']]), {'counted_cash': '1'}).status_code, 404
        )

    def test_requires_authentication(self):
        self.assertIn(APIClient().get(reverse('tillsession-list')).status_code, (401, 403))
        self.assertIn(APIClient().get(reverse('sale-list')).status_code, (401, 403))


class SaleTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.session = self.open()

    def test_cash_sale_gives_change_and_takes_stock(self):
        product = self.stocked(price='2.50', batches=((10, 30),))
        response = self.sell(self.session['id'], [(product, 3)], 'cash', '10.00')
        self.assertEqual(response.status_code, 201, response.content)
        sale = response.json()
        self.assertEqual(sale['total'], '7.50')
        self.assertEqual(sale['amount_tendered'], '10.00')
        self.assertEqual(sale['change_due'], '2.50')
        self.assertEqual(sale['status'], 'completed')
        self.assertEqual(sale['cashier_email'], self.cashier.email)
        self.assertEqual(sale['receipt_number'], f"S-{sale['id']:06d}")
        self.assertEqual(
            sale['items'],
            [{'id': sale['items'][0]['id'], 'product': product.pk, 'product_name': product.name,
              'quantity': 3, 'unit_price': '2.50', 'line_total': '7.50'}],
        )
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 7)
        self.assertEqual(StockBatch.objects.get(product=product).quantity, 7)
        movement = StockMovement.objects.get(batch__product=product)
        self.assertEqual((movement.movement_type, movement.quantity), ('outbound', 3))

    def test_card_sale_needs_no_tendered_amount(self):
        product = self.stocked(price='1.99')
        sale = self.sell(self.session['id'], [(product, 2)], 'card').json()
        self.assertEqual(sale['total'], '3.98')
        self.assertEqual(sale['amount_tendered'], '3.98')
        self.assertEqual(sale['change_due'], '0.00')

    def test_multiple_lines_and_repeated_products_are_merged(self):
        a, b = self.stocked(price='1.00'), self.stocked(price='2.00')
        sale = self.sell(self.session['id'], [(a, 1), (b, 2), (a, 2)], 'card').json()
        self.assertEqual(sale['total'], '7.00')
        self.assertEqual(sorted((i['product'], i['quantity']) for i in sale['items']), sorted([(a.pk, 3), (b.pk, 2)]))

    def test_not_enough_cash_is_rejected_and_stock_untouched(self):
        product = self.stocked(price='5.00')
        response = self.sell(self.session['id'], [(product, 2)], 'cash', '9.99')
        self.assertEqual(response.status_code, 400)
        self.assertIn('amount_tendered', response.json())
        self.assertEqual(self.sell(self.session['id'], [(product, 2)], 'cash').status_code, 400)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 10)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_exact_cash_is_fine(self):
        product = self.stocked(price='5.00')
        self.assertEqual(self.sell(self.session['id'], [(product, 2)], 'cash', '10.00').json()['change_due'], '0.00')

    def test_oldest_expiry_is_sold_first_across_batches(self):
        product = self.stocked(batches=((2, 5), (5, 40), (4, 10)))
        self.sell(self.session['id'], [(product, 7)], 'card')
        remaining = {b.expiration_date: b.quantity for b in StockBatch.objects.filter(product=product)}
        today = timezone.localdate()
        self.assertEqual(remaining[today + timedelta(days=5)], 0)
        self.assertEqual(remaining[today + timedelta(days=10)], 0)
        self.assertEqual(remaining[today + timedelta(days=40)], 4)
        self.assertEqual(StockMovement.objects.filter(batch__product=product).count(), 3)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 4)

    def test_expired_stock_is_not_sold(self):
        product = self.stocked(batches=((5, -1), (3, 10)))
        response = self.sell(self.session['id'], [(product, 4)], 'card')
        self.assertEqual(response.status_code, 400)
        self.assertIn('only 3 in date', str(response.json()))
        self.assertEqual(self.sell(self.session['id'], [(product, 3)], 'card').status_code, 201)

    def test_insufficient_stock_rejects_the_whole_sale(self):
        plenty, scarce = self.stocked(batches=((10, 30),)), self.stocked(batches=((1, 30),))
        response = self.sell(self.session['id'], [(plenty, 2), (scarce, 5)], 'card')
        self.assertEqual(response.status_code, 400)
        self.assertIn(scarce.name, str(response.json()['items']))
        plenty.refresh_from_db()
        self.assertEqual(plenty.stock_quantity, 10)
        self.assertEqual(Sale.objects.count(), 0)

    def test_product_without_batches_cannot_be_sold(self):
        product = ProductFactory(company=self.company, stock_quantity=20)
        self.assertEqual(self.sell(self.session['id'], [(product, 1)], 'card').status_code, 400)

    def test_client_cannot_set_the_price(self):
        product = self.stocked(price='3.00')
        response = self.client.post(
            reverse('sale-list'),
            {'session': self.session['id'], 'payment_method': 'card',
             'items': [{'product': product.pk, 'quantity': 1, 'unit_price': '0.01'}]},
            format='json',
        )
        self.assertEqual(response.json()['total'], '3.00')

    def test_validation(self):
        product = self.stocked()
        for items in ([], [(product, 0)], [(product, -1)]):
            self.assertEqual(self.sell(self.session['id'], items, 'card').status_code, 400, items)
        self.assertEqual(self.sell(self.session['id'], [(product, 1)], 'bitcoin').status_code, 400)

    def test_cannot_sell_on_a_closed_session(self):
        product = self.stocked()
        self.client.post(reverse('tillsession-close', args=[self.session['id']]), {'counted_cash': '50'})
        response = self.sell(self.session['id'], [(product, 1)], 'card')
        self.assertEqual(response.status_code, 400)
        self.assertIn('closed', str(response.json()))

    def test_other_companies_cannot_sell_on_our_session_or_use_our_products(self):
        product = self.stocked()
        outsider = self.as_user(UserFactory())
        self.assertEqual(self.sell(self.session['id'], [(product, 1)], 'card', client=outsider).status_code, 400)
        # A foreign product on our own session is not accepted either.
        foreign = ProductFactory(company=CompanyFactory(), stock_quantity=0)
        StockBatchFactory(product=foreign, quantity=5)
        self.assertEqual(self.sell(self.session['id'], [(foreign, 1)], 'card').status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)

    def test_resending_a_sale_with_the_same_client_ref_does_not_charge_twice(self):
        product = self.stocked(price='2.00')
        ref = '6f1c1a54-6a52-4a3c-9d9e-0e0d2a6d4b11'
        first = self.sell(self.session['id'], [(product, 2)], 'card', client_ref=ref)
        again = self.sell(self.session['id'], [(product, 2)], 'card', client_ref=ref)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.json()['id'], again.json()['id'])
        self.assertEqual(Sale.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 8)
        other = self.sell(self.session['id'], [(product, 1)], 'card', client_ref='0b8e1e0e-1f6f-4a5e-8f55-3f1f0f0f0f0f')
        self.assertEqual(other.status_code, 201)

    def test_bad_client_ref_is_rejected(self):
        product = self.stocked()
        self.assertEqual(self.sell(self.session['id'], [(product, 1)], 'card', client_ref='nope').status_code, 400)

    def test_list_filters_and_scoping(self):
        product = self.stocked(batches=((50, 30),))
        cash = self.sell(self.session['id'], [(product, 1)], 'cash', '5').json()
        card = self.sell(self.session['id'], [(product, 1)], 'card').json()
        self.client.post(reverse('sale-void', args=[card['id']]), {'reason': 'oops'})
        url = reverse('sale-list')
        self.assertEqual(self.client.get(url).json()['count'], 2)
        self.assertEqual([s['id'] for s in self.client.get(url, {'payment_method': 'cash'}).json()['results']], [cash['id']])
        self.assertEqual([s['id'] for s in self.client.get(url, {'status': 'voided'}).json()['results']], [card['id']])
        self.assertEqual(self.client.get(url, {'session': self.session['id']}).json()['count'], 2)
        self.assertEqual(self.client.get(url, {'till': self.till.pk}).json()['count'], 2)
        self.assertEqual(self.client.get(url, {'session': 999999}).json()['count'], 0)
        outsider = self.as_user(UserFactory())
        self.assertEqual(outsider.get(url).json()['count'], 0)
        self.assertEqual(outsider.get(reverse('sale-detail', args=[cash['id']])).status_code, 404)
        self.assertEqual(self.client.get(reverse('sale-detail', args=[cash['id']])).status_code, 200)


class VoidTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.session = self.open()
        self.product = self.stocked(price='2.00', batches=((3, 5), (10, 30)))
        self.sale = self.sell(self.session['id'], [(self.product, 5)], 'cash', '10.00').json()

    def void(self, client=None, reason='customer changed mind'):
        return (client or self.client).post(reverse('sale-void', args=[self.sale['id']]), {'reason': reason})

    def test_void_returns_stock_to_the_same_batches(self):
        response = self.void()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body['status'], body['void_reason']), ('voided', 'customer changed mind'))
        self.assertIsNotNone(body['voided_at'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 13)
        today = timezone.localdate()
        quantities = {b.expiration_date: b.quantity for b in StockBatch.objects.filter(product=self.product)}
        self.assertEqual(quantities, {today + timedelta(days=5): 3, today + timedelta(days=30): 10})
        kinds = sorted(StockMovement.objects.filter(batch__product=self.product).values_list('movement_type', flat=True))
        self.assertEqual(kinds, ['inbound', 'inbound', 'outbound', 'outbound'])

    def test_voided_sales_leave_the_session_totals(self):
        self.void()
        summary = self.client.get(reverse('tillsession-detail', args=[self.session['id']])).json()['summary']
        self.assertEqual(summary['sales_count'], 0)
        self.assertEqual(summary['sales_total'], '0.00')
        self.assertEqual((summary['voided_count'], summary['voided_total']), (1, '10.00'))
        self.assertEqual(summary['expected_cash'], '50.00')

    def test_cannot_void_twice(self):
        self.assertEqual(self.void().status_code, 200)
        self.assertEqual(self.void().status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 13)

    def test_another_cashier_cannot_void_but_staff_can(self):
        colleague = self.as_user(UserFactory(company=self.company))
        self.assertEqual(self.void(colleague).status_code, 403)
        self.assertEqual(self.void(self.as_user(self.manager)).status_code, 200)

    def test_cannot_void_after_the_session_is_closed(self):
        self.client.post(reverse('tillsession-close', args=[self.session['id']]), {'counted_cash': '60'})
        response = self.void()
        self.assertEqual(response.status_code, 400)
        self.assertIn('closed', str(response.json()))

    def test_other_company_gets_404(self):
        self.assertEqual(self.void(self.as_user(UserFactory())).status_code, 404)
