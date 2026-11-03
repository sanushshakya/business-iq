# inventory/test_breakdown_models.py

"""G21: breakdown models — tenancy registry and the ledger's link to a breakdown."""

from django.test import TestCase

from common.tenancy import TENANT_LOOKUPS


class BreakdownModelTests(TestCase):
    def test_breakdowns_are_tenant_scoped(self):
        self.assertEqual(TENANT_LOOKUPS['inventory.Breakdown'], 'company')
        self.assertEqual(TENANT_LOOKUPS['inventory.BreakdownLine'], 'breakdown__company')

    def test_reference(self):
        from inventory.models import Breakdown
        self.assertEqual(Breakdown(number=12).reference, 'BD-000012')
        self.assertEqual(Breakdown().reference, 'BD-new')
