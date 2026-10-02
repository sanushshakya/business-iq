# common/tests/test_alerts.py

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.models import DemandAlert, StockAlert
from common.tasks import check_low_stock, scan_demand_alerts

from .factories import CompanyFactory, ProductFactory, UserFactory


class DemandAlertModelTests(TestCase):
    def test_str(self):
        alert = DemandAlert(product='Product A', branch='Branch X', requested_qty=10)
        self.assertEqual(str(alert), 'Product A in Branch X - 10 units requested')

    def test_defaults(self):
        alert = DemandAlert.objects.create(company=CompanyFactory(), product='B', branch='Y', requested_qty=5)
        self.assertFalse(alert.is_handled)
        self.assertLess(abs(timezone.now() - alert.created_at), timedelta(seconds=5))


class ScanDemandAlertsTests(TestCase):
    def test_only_stale_unhandled_alerts_are_handled(self):
        company = CompanyFactory()
        old = DemandAlert.objects.create(company=company, product='A', branch='X', requested_qty=1)
        new = DemandAlert.objects.create(company=company, product='B', branch='X', requested_qty=1)
        DemandAlert.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))

        self.assertEqual(scan_demand_alerts(), 1)

        old.refresh_from_db()
        new.refresh_from_db()
        self.assertTrue(old.is_handled)
        self.assertFalse(new.is_handled)


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
        self.assertEqual(len(response.json()), 1)

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
