from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from tenants.models import Branch, Till
from tenants.operator_codes import CODE_RE, hash_code
from tenants.roles import DEMAND, FREIGHT, STOCK, SUPPLIER, TILL

User = get_user_model()
ROOMY_RATES = {'login': '1000/min', 'refresh': '1000/min', 'code_login': '1000/min'}


class OperatorTestCase(TestCase):
    def setUp(self):
        cache.clear()  # the throttles count per client and the cache outlives a test
        self.enterContext(mock.patch.object(ScopedRateThrottle, 'THROTTLE_RATES', ROOMY_RATES))
        self.company = CompanyFactory()
        self.admin = UserFactory(company=self.company, is_staff=True)
        self.admin_client = self.client_for(self.admin)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def create_operator(self, name='Amy', client=None, role=TILL):
        response = (client or self.admin_client).post(reverse('operator-list'), {'name': name, 'role': role})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def code_login(self, code):
        return APIClient().post(reverse('code-login'), {'code': code}, format='json')

    def operator_client(self, code):
        response = self.code_login(code)
        self.assertEqual(response.status_code, 200, response.content)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access_token']}")
        return client, response.json()


class ManagingOperatorsTests(OperatorTestCase):
    def test_create_returns_a_generated_code_once_and_stores_only_a_hash(self):
        body = self.create_operator('Amy')
        self.assertRegex(body['code'], CODE_RE)
        self.assertEqual((body['name'], body['is_active']), ('Amy', True))
        user = User.objects.get(pk=body['id'])
        self.assertEqual(user.role, TILL)
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(user.company, self.company)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.code_hash, hash_code(body['code']))
        self.assertNotIn(body['code'], user.code_hash)

    def test_the_code_is_never_listed_again(self):
        code = self.create_operator('Amy')['code']
        for response in (
            self.admin_client.get(reverse('operator-list')),
            self.admin_client.get(reverse('operator-detail', args=[User.objects.exclude(role='').get().pk])),
        ):
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(code, response.content.decode())
            self.assertNotIn('code_hash', response.content.decode())

    def test_the_admin_cannot_choose_the_code(self):
        response = self.admin_client.post(reverse('operator-list'), {'name': 'Amy', 'role': 'till', 'code': '12345678'})
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.json()['code'], '12345678')

    def test_codes_are_unique_even_when_the_generator_repeats(self):
        first = self.create_operator('Amy')['code']
        with mock.patch('tenants.operator_codes.generate_code', side_effect=[first, first, '87654321']):
            second = self.create_operator('Ben')['code']
        self.assertEqual(second, '87654321')

    def test_name_is_required(self):
        for body in ({}, {'name': '', 'role': 'till'}, {'name': '   ', 'role': 'till'}, {'name': 'Amy'}, {'name': 'Amy', 'role': ''},
                     {'name': 'Amy', 'role': 'boss'}):
            self.assertEqual(self.admin_client.post(reverse('operator-list'), body).status_code, 400, body)

    def test_only_staff_can_manage_till_users(self):
        cashier = self.client_for(UserFactory(company=self.company))
        self.assertEqual(cashier.get(reverse('operator-list')).status_code, 403)
        self.assertEqual(cashier.post(reverse('operator-list'), {'name': 'X', 'role': 'till'}).status_code, 403)
        self.assertIn(APIClient().get(reverse('operator-list')).status_code, (401, 403))

    def test_list_is_scoped_to_the_company_and_leaves_out_regular_users(self):
        self.create_operator('Amy')
        other = UserFactory(company=CompanyFactory(), is_staff=True)
        theirs = self.create_operator('Zed', client=self.client_for(other))
        mine = self.admin_client.get(reverse('operator-list')).json()
        self.assertEqual([u['name'] for u in mine['results']], ['Amy'])
        self.assertEqual(self.admin_client.get(reverse('operator-detail', args=[theirs['id']])).status_code, 404)
        self.assertEqual(
            self.admin_client.patch(reverse('operator-detail', args=[theirs['id']]), {'is_active': False}).status_code, 404
        )
        self.assertEqual(
            self.admin_client.post(reverse('operator-reset-code', args=[theirs['id']])).status_code, 404
        )

    def test_rename_but_not_delete(self):
        pk = self.create_operator('Amy')['id']
        url = reverse('operator-detail', args=[pk])
        self.assertEqual(self.admin_client.patch(url, {'name': 'Amy B'}).json()['name'], 'Amy B')
        self.assertEqual(self.admin_client.delete(url).status_code, 405)

    def test_cannot_promote_an_operator_through_the_api(self):
        pk = self.create_operator('Amy')['id']
        self.admin_client.patch(reverse('operator-detail', args=[pk]), {'is_staff': True, 'role': ''})
        user = User.objects.get(pk=pk)
        self.assertEqual(user.role, TILL)
        self.assertFalse(user.is_staff)


class CodeLoginTests(OperatorTestCase):
    def test_signing_in_with_the_code(self):
        body = self.create_operator('Amy')
        client, tokens = self.operator_client(body['code'])
        self.assertEqual(tokens['token_type'].lower(), 'bearer')
        me = client.get(reverse('me')).json()
        self.assertEqual((me['name'], me['role'], me['role_label'], me['is_staff'], me['email']), ('Amy', 'till', 'Till operator', False, None))
        self.assertEqual(me['company'], self.company.name)
        self.assertIsNotNone(User.objects.get(pk=body['id']).last_login)

    def test_bad_codes_all_get_the_same_answer(self):
        code = self.create_operator('Amy')['code']
        wrong = '00000000' if code != '00000000' else '11111111'
        answers = set()
        for attempt in (wrong, code[:7], code + '1', 'abcdefgh', '', ' ', '1234 5678', None):
            response = self.code_login(attempt)
            self.assertEqual(response.status_code, 400, attempt)
            answers.add(response.content)
        self.assertEqual(len(answers), 1)

    def test_codes_work_with_stray_spaces(self):
        code = self.create_operator('Amy')['code']
        self.assertEqual(self.code_login(f' {code} ').status_code, 200)

    def test_a_deactivated_operator_cannot_sign_in_and_loses_their_session(self):
        body = self.create_operator('Amy')
        client, tokens = self.operator_client(body['code'])
        self.assertEqual(client.get(reverse('me')).status_code, 200)

        response = self.admin_client.patch(reverse('operator-detail', args=[body['id']]), {'is_active': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.code_login(body['code']).status_code, 400)
        self.assertEqual(client.get(reverse('me')).status_code, 401)
        refresh = APIClient().post(reverse('token-refresh'), {'refresh_token': tokens['refresh_token']})
        self.assertEqual(refresh.status_code, 401)

        self.admin_client.patch(reverse('operator-detail', args=[body['id']]), {'is_active': True})
        self.assertEqual(self.code_login(body['code']).status_code, 200)

    def test_resetting_the_code_ends_the_session_and_retires_the_old_code(self):
        body = self.create_operator('Amy')
        _client, tokens = self.operator_client(body['code'])
        response = self.admin_client.post(reverse('operator-reset-code', args=[body['id']]))
        self.assertEqual(response.status_code, 200)
        new = response.json()['code']
        self.assertRegex(new, CODE_RE)
        self.assertNotEqual(new, body['code'])
        self.assertEqual(self.code_login(body['code']).status_code, 400)
        self.assertEqual(self.code_login(new).status_code, 200)
        refresh = APIClient().post(reverse('token-refresh'), {'refresh_token': tokens['refresh_token']})
        self.assertEqual(refresh.status_code, 401)

    def test_a_regular_users_password_or_a_till_users_email_cannot_be_used_the_other_way(self):
        body = self.create_operator('Amy')
        placeholder = User.objects.get(pk=body['id']).email
        self.assertEqual(
            APIClient().post(reverse('login'), {'username': placeholder, 'password': ''}).status_code, 400
        )
        self.assertEqual(
            APIClient().post(reverse('login'), {'username': placeholder, 'password': body['code']}).status_code, 400
        )
        regular = UserFactory(company=self.company)  # has a password but no code
        self.assertEqual(self.code_login(regular.email).status_code, 400)

    def test_sign_in_is_rate_limited(self):
        with mock.patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {**ROOMY_RATES, 'code_login': '3/min'}):
            statuses = [self.code_login('00000000').status_code for _ in range(5)]
        self.assertEqual(statuses[:3], [400, 400, 400])
        self.assertEqual(statuses[3:], [429, 429])


class TillRoleAccessTests(OperatorTestCase):
    def setUp(self):
        super().setUp()
        self.operator = self.create_operator('Amy')
        self.client, _tokens = self.operator_client(self.operator['code'])

    def test_blocked_from_everything_but_the_till(self):
        product = ProductFactory(company=self.company)
        checks = [
            ('get', reverse('operator-list')),
            ('post', reverse('operator-list')),
            ('get', reverse('stockbatch-list')),
            ('post', reverse('stockmovement-list')),
            ('get', reverse('supplier-list')),
            ('post', reverse('product-list')),
            ('patch', reverse('product-detail', args=[product.pk])),
            ('delete', reverse('product-detail', args=[product.pk])),
            ('get', reverse('invitation-list')),
            ('post', reverse('till-list')),
            ('post', reverse('branch-list')),
            ('patch', reverse('my-company')),
        ]
        for method, url in checks:
            self.assertEqual(getattr(self.client, method)(url, {}).status_code, 403, (method, url))

    def test_can_read_what_the_till_needs(self):
        ProductFactory(company=self.company)
        for name in ('product-list', 'till-list', 'branch-list', 'my-company', 'tillsession-list', 'sale-list', 'me'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_full_shift_with_the_operators_name_on_the_receipt(self):
        branch = Branch.objects.create(company=self.company, name='High Street', address='1 High St')
        till = Till.objects.create(branch=branch, number=1, device_id='d-1')
        product = ProductFactory(company=self.company, price=Decimal('2.00'), stock_quantity=10)
        StockBatchFactory(product=product, quantity=10, expiration_date=timezone.localdate() + timedelta(days=30))

        session = self.client.post(reverse('tillsession-list'), {'till': till.pk, 'opening_float': '20.00'})
        self.assertEqual(session.status_code, 201, session.content)
        self.assertEqual(session.json()['opened_by_name'], 'Amy')
        sale = self.client.post(
            reverse('sale-list'),
            {'session': session.json()['id'], 'payment_method': 'card', 'items': [{'product': product.pk, 'quantity': 2}]},
            format='json',
        )
        self.assertEqual(sale.status_code, 201, sale.content)
        self.assertEqual(sale.json()['cashier_name'], 'Amy')
        voided = self.client.post(reverse('sale-void', args=[sale.json()['id']]), {'reason': 'oops'})
        self.assertEqual(voided.status_code, 200, voided.content)
        closed = self.client.post(reverse('tillsession-close', args=[session.json()['id']]), {'counted_cash': '20.00'})
        self.assertEqual(closed.status_code, 200, closed.content)

    def test_sees_only_their_own_sessions_and_sales(self):
        branch = Branch.objects.create(company=self.company, name='High Street', address='1 High St')
        till_a = Till.objects.create(branch=branch, number=1, device_id='d-a')
        till_b = Till.objects.create(branch=branch, number=2, device_id='d-b')
        product = ProductFactory(company=self.company, price=Decimal('2.00'), stock_quantity=10)
        StockBatchFactory(product=product, quantity=10, expiration_date=timezone.localdate() + timedelta(days=30))

        mine = self.client.post(reverse('tillsession-list'), {'till': till_a.pk}).json()
        theirs = self.admin_client.post(reverse('tillsession-list'), {'till': till_b.pk}).json()
        their_sale = self.admin_client.post(
            reverse('sale-list'),
            {'session': theirs['id'], 'payment_method': 'card', 'items': [{'product': product.pk, 'quantity': 1}]},
            format='json',
        ).json()

        sessions = [s['id'] for s in self.client.get(reverse('tillsession-list')).json()['results']]
        self.assertEqual(sessions, [mine['id']])
        self.assertEqual(self.client.get(reverse('tillsession-detail', args=[theirs['id']])).status_code, 404)
        self.assertEqual(self.client.get(reverse('sale-list')).json()['count'], 0)
        self.assertEqual(self.client.get(reverse('sale-detail', args=[their_sale['id']])).status_code, 404)
        self.assertEqual(self.client.post(reverse('sale-void', args=[their_sale['id']])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('tillsession-close', args=[theirs['id']]), {'counted_cash': '1'}).status_code, 404
        )

    def test_regular_staff_still_have_full_access(self):
        self.assertEqual(self.admin_client.get(reverse('stockbatch-list')).status_code, 200)
        self.assertEqual(self.admin_client.get(reverse('operator-list')).status_code, 200)


class RoleAccessTests(OperatorTestCase):
    """Each role gets its own area and nothing else."""

    # path -> roles whose operators may GET it (everything else must answer 403)
    READS = {
        '/inventory/products/': {TILL, DEMAND, SUPPLIER, STOCK},
        '/inventory/categories/': {DEMAND, SUPPLIER, STOCK},
        '/inventory/batches/': {STOCK},
        '/inventory/movements/': {STOCK},
        '/inventory/orders/': {STOCK},
        '/inventory/suppliers/': {SUPPLIER},
        '/logistics/suppliers/': {SUPPLIER},
        '/logistics/alternative-suppliers/': {SUPPLIER},
        '/pricing/invoices/': {SUPPLIER},
        '/pricing/price-changes/': {STOCK},
        '/pricing/subscriptions/': set(),
        '/logistics/freight-alerts/': {FREIGHT},
        '/logistics/providers/': {FREIGHT},
        '/logistics/deliveries/': {FREIGHT},
        '/logistics/freight-rates/': {FREIGHT},
        '/common/api/alerts/stock/': {DEMAND, STOCK},
        '/common/api/alerts/demand/': {DEMAND},
        '/calendar/upcoming/': {DEMAND},
        '/calendar/events/': {DEMAND},
        '/demand/events/': {DEMAND},
        '/demand/demands/': {DEMAND},
        '/tenants/tills/': {TILL},
        '/tenants/branches/': {TILL},
        '/sales/sales/': {TILL},
        '/sales/sessions/': {TILL},
        '/tenants/operators/': set(),
        '/auth/invitations/': set(),
        '/common/api/settings/': set(),
        '/sync/tasks/': set(),
        '/tenants/company/': {TILL, DEMAND, FREIGHT, SUPPLIER, STOCK},
        '/auth/me/': {TILL, DEMAND, FREIGHT, SUPPLIER, STOCK},
    }

    def test_every_role_reads_exactly_its_own_area(self):
        for role in (TILL, DEMAND, FREIGHT, SUPPLIER, STOCK):
            client, _ = self.operator_client(self.create_operator(f'{role} op', role=role)['code'])
            for path, roles in self.READS.items():
                status = client.get(path).status_code
                if role in roles:
                    self.assertEqual(status, 200, (role, path))
                else:
                    self.assertEqual(status, 403, (role, path))

    def test_stock_operators_see_price_changes_but_cannot_decide_them(self):
        from decimal import Decimal

        from pricing.models import PriceChangeLog

        log = PriceChangeLog.objects.create(
            product=ProductFactory(company=self.company), old_price=Decimal('2.00'), new_price=Decimal('1.50')
        )
        stock, _ = self.operator_client(self.create_operator('S', role=STOCK)['code'])
        for action in ('approve', 'reject'):
            self.assertEqual(stock.post(f'/pricing/price-changes/{log.pk}/{action}/').status_code, 403, action)
        log.refresh_from_db()
        self.assertEqual((log.is_approved, log.is_rejected), (False, False))

    def test_writes_follow_the_role(self):
        demand, _ = self.operator_client(self.create_operator('D', role=DEMAND)['code'])
        freight, _ = self.operator_client(self.create_operator('F', role=FREIGHT)['code'])
        till, _ = self.operator_client(self.create_operator('T', role=TILL)['code'])
        stock, _ = self.operator_client(self.create_operator('S', role=STOCK)['code'])

        self.assertEqual(demand.post('/common/api/alerts/demand/create/', {}).status_code, 400)  # allowed, invalid body
        self.assertEqual(till.post('/common/api/alerts/demand/create/', {}).status_code, 403)
        self.assertEqual(freight.post('/logistics/providers/', {}).status_code, 400)
        self.assertEqual(demand.post('/logistics/providers/', {}).status_code, 403)
        self.assertEqual(stock.post('/inventory/batches/', {}).status_code, 400)
        self.assertEqual(freight.post('/inventory/batches/', {}).status_code, 403)
        # Never anything that changes a company, its users or its settings.
        for client in (demand, freight, till, stock):
            self.assertEqual(client.post('/tenants/operators/', {'name': 'x', 'role': 'till'}).status_code, 403)
            self.assertEqual(client.patch('/tenants/company/', {'name': 'x'}).status_code, 403)

    def test_a_freight_operator_manages_deliveries_for_their_own_company_only(self):
        from datetime import date

        from logistics.models import Delivery, LogisticProvider

        mine = LogisticProvider.objects.create(company=self.company, name='DHL', address='x', phone_number='1')
        theirs = LogisticProvider.objects.create(company=CompanyFactory(), name='Other', address='x', phone_number='1')
        Delivery.objects.create(provider=mine, shipment_date=date.today())
        Delivery.objects.create(provider=theirs, shipment_date=date.today())
        client, _ = self.operator_client(self.create_operator('Fred', role=FREIGHT)['code'])
        response = client.get('/logistics/deliveries/')
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(client.get('/logistics/providers/').json()['count'], 1)

    def test_changing_a_role_changes_access_and_ends_the_session(self):
        body = self.create_operator('Amy', role=TILL)
        client, tokens = self.operator_client(body['code'])
        self.assertEqual(client.get('/logistics/providers/').status_code, 403)

        response = self.admin_client.patch(reverse('operator-detail', args=[body['id']]), {'role': FREIGHT})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['role_label'], 'Freight manager')
        refresh = APIClient().post(reverse('token-refresh'), {'refresh_token': tokens['refresh_token']})
        self.assertEqual(refresh.status_code, 401)

        client, _ = self.operator_client(body['code'])
        self.assertEqual(client.get('/logistics/providers/').status_code, 200)
        self.assertEqual(client.get('/sales/sales/').status_code, 403)

    def test_invalid_roles_are_refused_on_update(self):
        body = self.create_operator('Amy', role=TILL)
        for role in ('', 'boss', 'staff'):
            response = self.admin_client.patch(reverse('operator-detail', args=[body['id']]), {'role': role})
            self.assertEqual(response.status_code, 400, role)
        self.assertEqual(User.objects.get(pk=body['id']).role, TILL)

    def test_the_list_shows_roles_and_filters_out_regular_users(self):
        self.create_operator('Amy', role=TILL)
        self.create_operator('Dee', role=DEMAND)
        names = {u['name']: u['role_label'] for u in self.admin_client.get(reverse('operator-list')).json()['results']}
        self.assertEqual(names, {'Amy': 'Till operator', 'Dee': 'Demand planner'})
