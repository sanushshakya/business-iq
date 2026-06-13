"""
common/tests/test_demand_alert.py

Unit tests for the DemandAlert model.
"""

import datetime
from django.test import TestCase
from common.models import DemandAlert

class TestDemandAlert(TestCase):
    def test_str_representation(self):
        """Test the string representation of the DemandAlert model."""
        alert = DemandAlert(product='Product A', branch='Branch X', requested_qty=10)
        self.assertEqual(str(alert), 'Product A in Branch X - 10 units requested')

    def test_created_at_default(self):
        """Test that created_at defaults to the current time if not specified."""
        alert = DemandAlert(product='Product B', branch='Branch Y', requested_qty=5)
        alert.save()
        self.assertAlmostEqual(alert.created_at, datetime.datetime.now(), delta=datetime.timedelta(seconds=1))

    def test_is_handled_initial_value(self):
        """Test that is_handled defaults to False if not specified."""
        alert = DemandAlert(product='Product C', branch='Branch Z', requested_qty=20)
        self.assertFalse(alert.is_handled)

    def test_save_method(self):
        """Test the save method of the DemandAlert model."""
        alert = DemandAlert(product='Product D', branch='Branch W', requested_qty=30, is_handled=True)
        alert.save()
        retrieved_alert = DemandAlert.objects.get(id=alert.id)
        self.assertEqual(retrieved_alert.product, 'Product D')
        self.assertEqual(retrieved_alert.branch, 'Branch W')
        self.assertEqual(retrieved_alert.requested_qty, 30)
        self.assertTrue(retrieved_alert.is_handled)

    def test_queryset_filtering(self):
        """Test querying DemandAlerts based on specific criteria."""
        alert1 = DemandAlert.objects.create(product='Product E', branch='Branch V', requested_qty=40, is_handled=False)
        alert2 = DemandAlert.objects.create(product='Product F', branch='Branch U', requested_qty=50, is_handled=True)

        handled_alerts = DemandAlert.objects.filter(is_handled=True)
        self.assertEqual(len(handled_alerts), 1)
        self.assertEqual(handled_alerts[0], alert2)

        unhandled_alerts = DemandAlert.objects.filter(is_handled=False)
        self.assertEqual(len(unhandled_alerts), 1)
        self.assertEqual(unhandled_alerts[0], alert1)