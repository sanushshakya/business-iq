# common/tests/test_alerts.py

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.models import DemandAlert, StockAlert
from common.tasks import check_low_stock

from .factories import CompanyFactory, ProductFactory, UserFactory


class DemandAlertModelTests(TestCase):
    def test_str(self):
        alert = DemandAlert(product='Product A', branch='Branch X', requested_qty=10)
        self.assertEqual(str(alert), 'Product A in Branch X - 10 units requested')

    def test_defaults(self):
        alert = DemandAlert.objects.create(company=CompanyFactory(), product='B', branch='Y', requested_qty=5)
        self.assertFalse(alert.is_handled)
        self.assertLess(abs(timezone.now() - alert.created_at), timedelta(seconds=5))


class CheckLowStockTests(TestCase):
    def test_creates_one_alert_per_low_product(self):
        low = ProductFactory(stock_quantity=2, reorder_threshold=5)
        ProductFactory(stock_quantity=50, reorder_threshold=5)

        self.assertEqual(check_low_stock(), 1)
        self.assertEqual(StockAlert.objects.get().product, low)

    def test_does_not_duplicate_open_alerts(self):
        ProductFactory(stock_quantity=0, reorder_threshold=5)
        check_low_stock()
        self.assertEqual(check_low_stock(), 0)
        self.assertEqual(StockAlert.objects.count(), 1)


class AlertApiTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_requires_authentication(self):
        response = APIClient().get(reverse('stock-alert-list'))
        self.assertIn(response.status_code, (401, 403))

    def test_stock_alert_list_hides_dismissed(self):
        product = ProductFactory(company=self.user.company)
        StockAlert.objects.create(product=product, current_qty=1, threshold=5)
        StockAlert.objects.create(product=product, current_qty=1, threshold=5, is_dismissed=True)
        response = self.client.get(reverse('stock-alert-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 1)

    def test_create_and_dismiss_demand_alert(self):
        response = self.client.post(
            reverse('demand-alert-create'), {'product': 'P', 'branch': 'B', 'requested_qty': 3}
        )
        self.assertEqual(response.status_code, 201)
        pk = response.json()['id']
        self.assertFalse(response.json()['is_handled'])
        self.assertEqual(DemandAlert.objects.get(pk=pk).company, self.user.company)

        response = self.client.post(reverse('demand-alert-dismiss', args=[pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DemandAlert.objects.get(pk=pk).is_handled)

    def test_demand_alert_list_requires_authentication(self):
        self.assertIn(APIClient().get(reverse('demand-alert-list')).status_code, (401, 403))

    def _demand_alerts(self):
        company = self.user.company
        older = DemandAlert.objects.create(company=company, product='Old', branch='B', requested_qty=1)
        newer = DemandAlert.objects.create(company=company, product='New', branch='B', requested_qty=2)
        handled = DemandAlert.objects.create(company=company, product='Done', branch='B', requested_qty=3, is_handled=True)
        DemandAlert.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=2))
        return older, newer, handled

    def _list(self, **params):
        response = self.client.get(reverse('demand-alert-list'), params)
        self.assertEqual(response.status_code, 200, response.content)
        return [row['product'] for row in response.json()['results']]

    def test_demand_alert_list_defaults_to_open_alerts_newest_first(self):
        self._demand_alerts()
        self.assertEqual(self._list(), ['New', 'Old'])

    def test_demand_alert_list_status_filter(self):
        self._demand_alerts()
        self.assertEqual(self._list(status='handled'), ['Done'])
        self.assertEqual(sorted(self._list(status='all')), ['Done', 'New', 'Old'])
        self.assertEqual(self._list(status='open'), ['New', 'Old'])

    def test_demand_alert_list_rejects_unknown_status(self):
        response = self.client.get(reverse('demand-alert-list'), {'status': 'bogus'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.json())

    def test_demand_alert_list_is_scoped_to_the_company(self):
        DemandAlert.objects.create(company=CompanyFactory(), product='Rival', branch='B', requested_qty=9)
        DemandAlert.objects.create(company=self.user.company, product='Mine', branch='B', requested_qty=1)
        self.assertEqual(self._list(status='all'), ['Mine'])

    def test_dismissed_alert_moves_from_open_to_handled(self):
        pk = self.client.post(reverse('demand-alert-create'), {'product': 'P', 'branch': 'B', 'requested_qty': 3}).json()['id']
        self.assertEqual(self._list(), ['P'])
        self.client.post(reverse('demand-alert-dismiss', args=[pk]))
        self.assertEqual(self._list(), [])
        self.assertEqual(self._list(status='handled'), ['P'])

    def test_demand_alert_list_is_paginated(self):
        for i in range(3):
            DemandAlert.objects.create(company=self.user.company, product=f'P{i}', branch='B', requested_qty=1)
        body = self.client.get(reverse('demand-alert-list')).json()
        self.assertEqual(set(body), {'count', 'next', 'previous', 'results'})
        self.assertEqual(body['count'], 3)

    def test_dismiss_unknown_alert_is_404(self):
        self.assertEqual(self.client.post(reverse('demand-alert-dismiss', args=[999])).status_code, 404)


class VerifyEmailTokenTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('verify-email-token')

    def test_valid_token(self):
        from common.services.verification_token_service import VerificationTokenService

        token = VerificationTokenService.generate_token(42)
        response = self.client.post(self.url, {'token': token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user_id'], 42)

    def test_invalid_token(self):
        response = self.client.post(self.url, {'token': 'invalid-token'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid or expired token'})

    def test_missing_token(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Token is required'})


class PaginationAndErrorShapeTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_lists_are_paginated_in_stable_order(self):
        ProductFactory.create_batch(30, company=self.user.company)
        url = reverse('product-list')
        first = self.client.get(url).json()
        second = self.client.get(url, {'page': 2}).json()
        self.assertEqual(first['count'], 30)
        self.assertEqual(len(first['results']), 25)
        self.assertEqual(len(second['results']), 5)
        ids = [p['id'] for p in first['results'] + second['results']]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(self.client.get(url, {'page_size': 1000}).json()['results']), 30)  # capped at 100

    def test_not_found_uses_uniform_error_shape(self):
        response = self.client.get(reverse('product-detail', args=[99999]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['status_code'], 404)
        self.assertEqual(response.json()['code'], 'Http404')
        self.assertIn('message', response.json())

    def test_permission_denied_uses_uniform_error_shape(self):
        orphan = APIClient()
        orphan.force_authenticate(UserFactory(company=None))
        response = orphan.get(reverse('product-list'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(set(response.json()), {'code', 'message', 'status_code'})

    def test_validation_errors_keep_field_shape(self):
        response = self.client.post(reverse('product-list'), {})
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json())

    def test_custom_api_exception(self):
        from rest_framework.test import APIRequestFactory

        from common.exceptions import CustomAPIException, custom_exception_handler

        exc = CustomAPIException('boom', status_code=418, code='teapot')
        response = custom_exception_handler(exc, {'request': APIRequestFactory().get('/'), 'view': None})
        self.assertEqual(response.status_code, 418)
        self.assertEqual(response.data['code'], 'teapot')
        self.assertEqual(str(response.data['message']), 'boom')
