"""G36: card sales on a standalone terminal. The till records the terminal's auth code (and optionally the card's last
4 digits) on each card sale; closing a till asks for the terminal's end-of-day card total and keeps the card variance
next to the cash variance."""

from decimal import Decimal

from django.urls import reverse

from sales.models import Sale, TillSession
from sales.services import close_session, session_summary

from .test_sales import SalesTestCase


class CardSaleTests(SalesTestCase):
    def test_card_sale_records_the_auth_code_and_last_4(self):
        session = self.open()
        product = self.stocked('2.50')
        body = {
            'session': session['id'], 'payment_method': 'card', 'items': [{'product': product.pk, 'quantity': 2}],
            'card_auth_code': 'a1b2c3', 'card_last4': '4242',
        }
        response = self.client.post(reverse('sale-list'), body, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['card_auth_code'], response.json()['card_last4']), ('A1B2C3', '4242'))
        sale = Sale.objects.get(pk=response.json()['id'])
        self.assertEqual((sale.card_auth_code, sale.card_last4), ('A1B2C3', '4242'))

    def test_last_4_is_optional(self):
        session = self.open()
        response = self.sell(session['id'], [(self.stocked(), 1)], method='card', auth='XYZ789')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['card_last4'], '')

    def test_card_sale_needs_the_auth_code(self):
        session = self.open()
        for auth in (None, '', '   '):
            response = self.sell(session['id'], [(self.stocked(), 1)], method='card', auth=auth)
            self.assertEqual(response.status_code, 400, auth)
            self.assertIn('card_auth_code', response.json())
        self.assertFalse(Sale.objects.exists())

    def test_bad_auth_code_or_last_4(self):
        session = self.open()
        product = self.stocked()
        for extra, field in (
            ({'card_auth_code': 'AB1'}, 'card_auth_code'),  # under 4
            ({'card_auth_code': 'A' * 13}, 'card_auth_code'),
            ({'card_auth_code': 'AB-123'}, 'card_auth_code'),
            ({'card_auth_code': 'AUTH01', 'card_last4': '424'}, 'card_last4'),
            ({'card_auth_code': 'AUTH01', 'card_last4': '42a2'}, 'card_last4'),
            ({'card_auth_code': 'AUTH01', 'card_last4': '٤٢٤٢'}, 'card_last4'),  # not ASCII digits
            ({'card_auth_code': 'AUTH01', 'card_last4': '4242424242424242'}, 'card_last4'),  # never a card number
        ):
            body = {'session': session['id'], 'payment_method': 'card', 'items': [{'product': product.pk, 'quantity': 1}],
                    **extra}
            response = self.client.post(reverse('sale-list'), body, format='json')
            self.assertEqual(response.status_code, 400, extra)
            self.assertIn(field, response.json(), extra)

    def test_cash_sale_refuses_card_details(self):
        session = self.open()
        body = {
            'session': session['id'], 'payment_method': 'cash', 'amount_tendered': '10.00',
            'items': [{'product': self.stocked().pk, 'quantity': 1}], 'card_auth_code': 'AUTH01',
        }
        response = self.client.post(reverse('sale-list'), body, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('card_auth_code', response.json())


class CardVarianceTests(SalesTestCase):
    def card_sales(self, session_id, *prices):
        for price in prices:
            self.assertEqual(self.sell(session_id, [(self.stocked(price), 1)], method='card').status_code, 201)

    def close(self, session_id, counted_cash='50.00', **extra):
        return self.client.post(
            reverse('tillsession-close', args=[session_id]), {'counted_cash': counted_cash, **extra}, format='json'
        )

    def test_terminal_total_short(self):
        session = self.open('50.00')
        self.card_sales(session['id'], '10.00', '2.55')
        response = self.close(session['id'], counted_card='12.50')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body['expected_card'], body['counted_card']), ('12.55', '12.50'))
        self.assertEqual(body['summary']['card_variance'], '-0.05')
        self.assertEqual(body['summary']['variance'], '0.00')  # cash still balances

    def test_terminal_total_over_and_balanced(self):
        session = self.open()
        self.card_sales(session['id'], '3.33', '3.33', '3.34')
        self.assertEqual(self.close(session['id'], counted_card='10.01').json()['summary']['card_variance'], '0.01')
        other = self.open()
        self.card_sales(other['id'], '0.01', '0.99')
        self.assertEqual(self.close(other['id'], counted_card='1.00').json()['summary']['card_variance'], '0.00')

    def test_voided_card_sales_are_not_expected_on_the_terminal(self):
        session = self.open()
        self.card_sales(session['id'], '4.00')
        voided = self.sell(session['id'], [(self.stocked('6.00'), 1)], method='card').json()
        self.client.post(reverse('sale-void', args=[voided['id']]), {'reason': 'customer changed mind'})
        body = self.close(session['id'], counted_card='4.00').json()
        self.assertEqual((body['expected_card'], body['summary']['card_variance']), ('4.00', '0.00'))

    def test_no_terminal_total_means_no_card_variance(self):
        session = self.open()
        self.card_sales(session['id'], '5.00')
        body = self.close(session['id']).json()
        self.assertEqual((body['counted_card'], body['summary']['card_variance']), (None, None))
        self.assertEqual(body['expected_card'], '5.00')  # still frozen at close

    def test_open_session_shows_expected_card_but_no_variance(self):
        session = self.open()
        self.card_sales(session['id'], '1.50')
        summary = self.client.get(reverse('tillsession-detail', args=[session['id']])).json()['summary']
        self.assertEqual((summary['expected_card'], summary['card_variance']), ('1.50', None))

    def test_expected_card_is_frozen_at_close(self):
        """A card sale synced later (offline, G28) changes the live total, not what the terminal was compared with."""
        session = self.open()
        self.card_sales(session['id'], '2.00')
        self.close(session['id'], counted_card='2.00')
        closed = TillSession.objects.get(pk=session['id'])
        Sale.objects.create(session=closed, cashier=self.cashier, payment_method=Sale.CARD, total=Decimal('9.00'),
                            amount_tendered=Decimal('9.00'))
        summary = session_summary(closed)
        self.assertEqual((summary['expected_card'], summary['card_variance']), (Decimal('2.00'), Decimal('0.00')))
        self.assertEqual(summary['card_total'], Decimal('11.00'))

    def test_bad_terminal_total(self):
        session = self.open()
        for value in ('-1.00', 'abc', '1.005'):
            self.assertEqual(self.close(session['id'], counted_card=value).status_code, 400, value)
        self.assertTrue(TillSession.objects.get(pk=session['id']).is_open)

    def test_service_close_keeps_both_variances(self):
        session = TillSession.objects.get(pk=self.open('20.00')['id'])
        self.card_sales(session.pk, '7.25')
        self.sell(session.pk, [(self.stocked('3.00'), 1)], tendered='5.00')
        closed = close_session(session=session, user=self.cashier, counted_cash=Decimal('22.50'),
                               counted_card=Decimal('7.25'))
        summary = session_summary(closed)
        self.assertEqual(summary['variance'], Decimal('-0.50'))
        self.assertEqual(summary['card_variance'], Decimal('0.00'))
