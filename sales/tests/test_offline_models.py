# sales/tests/test_offline_models.py

"""G28: what an offline sale keeps (its offline number, when the till made it, the card auth code) and its
exceptions."""

from decimal import Decimal

from django.db import IntegrityError, transaction

from common.tenancy import TENANT_LOOKUPS
from sales.models import Sale, SaleException
from sales.tests.test_sales import SalesTestCase


class OfflineSaleModelTests(SalesTestCase):
    def sale(self, session_id, **fields):
        return Sale.objects.create(session_id=session_id, cashier=self.cashier, payment_method='cash',
                                   total=Decimal('1.00'), amount_tendered=Decimal('1.00'), **fields)

    def test_offline_number_unique_per_session(self):
        session = self.open()['id']
        self.sale(session, offline_number='HS-T01-ABC-000001')
        self.sale(session)  # online sales have none
        self.sale(session)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.sale(session, offline_number='HS-T01-ABC-000001')

    def test_exception_is_tenant_owned_through_its_sale(self):
        self.assertEqual(TENANT_LOOKUPS['sales.SaleException'], 'sale__session__till__branch__company')
        sale = self.sale(self.open()['id'])
        exception = SaleException.objects.create(sale=sale, kind=SaleException.STOCK, detail='Fake: 2 short',
                                                 quantity=2)
        self.assertIsNone(exception.resolved_at)
        self.assertEqual(str(exception), f'{sale.receipt_number}: stock')
