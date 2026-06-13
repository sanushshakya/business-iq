"""
common/tests/test_scan_demand_alert.py

This file contains unit tests for the scan_demand_alerts task in the common app,
ensuring it calculates demand alerts correctly based on specific criteria.
"""

import unittest
from datetime import timedelta
from django.utils import timezone
from mock import patch, MagicMock
from ..models import DemandAlert
from .tasks import scan_demand_alerts

class TestScanDemandAlerts(unittest.TestCase):
    """
    Unit tests for the scan_demand_alerts task.
    """

    @patch('common.tasks.get_current_branch')
    def test_scan_demand_alerts(self, mock_get_current_branch):
        """
        Test that the scan_demand_alerts task correctly identifies and handles demand alerts based on criteria.

        Args:
            mock_get_current_branch (MagicMock): Mock object for get_current_branch function.
        """

        # Setup mock data
        current_branch = MagicMock()
        mock_get_current_branch.return_value = current_branch

        product1 = DemandAlert.objects.create(
            product='Product A',
            branch=current_branch,
            requested_qty=10,
            created_at=timezone.now() - timedelta(days=2),
            is_handled=False
        )

        product2 = DemandAlert.objects.create(
            product='Product B',
            branch=current_branch,
            requested_qty=5,
            created_at=timezone.now() - timedelta(hours=1),
            is_handled=False
        )

        # Call the task
        scan_demand_alerts()

        # Assert that only Product A is handled (since it's older than 24 hours)
        product1.refresh_from_db()
        product2.refresh_from_db()
        self.assertTrue(product1.is_handled)
        self.assertFalse(product2.is_handled)

if __name__ == '__main__':
    unittest.main()