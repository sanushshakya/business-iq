# sales/tests/test_exception_review.py

"""G29: managers review the exceptions offline sales raised when they were synced (G28) and mark them handled."""

from decimal import Decimal

from django.urls import reverse

from common.tests.factories import CompanyFactory, UserFactory
from sales.models import Sale, SaleException, TillSession
from sales.tests.test_sales import SalesTestCase, make_till

D = Decimal


class ExceptionReviewTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        session = self.open()['id']
        self.sale = Sale.objects.create(session_id=session, cashier=self.cashier, payment_method='cash',
                                        total=D('4.00'), amount_tendered=D('4.00'), offline_number='HS-T01-ABC-000001')
        self.rice = self.stocked(name='Fake Rice')
        self.price = SaleException.objects.create(sale=self.sale, kind='price', product=self.rice,
                                                  expected=D('5.00'), charged=D('4.00'))
        self.stock = SaleException.objects.create(sale=self.sale, kind='stock', product=self.rice, quantity=2,
                                                  detail='Fake: short')
        their_till = make_till(CompanyFactory())
        theirs = Sale.objects.create(
            session=TillSession.objects.create(till=their_till, opened_by=self.manager), cashier=self.manager,
            payment_method='cash', total=D('1.00'), amount_tendered=D('1.00'))
        SaleException.objects.create(sale=theirs, kind='stock')
        self.staff = self.as_user(self.manager)

    def test_lists_open_exceptions_of_the_company_only(self):
        response = self.staff.get(reverse('saleexception-list'))
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()['results']
        self.assertEqual({r['id'] for r in rows}, {self.price.pk, self.stock.pk})
        row = next(r for r in rows if r['id'] == self.price.pk)
        self.assertEqual(
            {k: row[k] for k in ('kind', 'kind_label', 'receipt_number', 'offline_number', 'product_name',
                                 'expected', 'charged', 'resolved_at')},
            {'kind': 'price', 'kind_label': 'Price differs', 'receipt_number': self.sale.receipt_number,
             'offline_number': 'HS-T01-ABC-000001', 'product_name': 'Fake Rice', 'expected': '5.00',
             'charged': '4.00', 'resolved_at': None})

    def test_resolve_and_filter(self):
        response = self.staff.post(reverse('saleexception-resolve', args=[self.price.pk]), {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['resolved_by_name'], self.manager.name or self.manager.email)
        open_ids = [r['id'] for r in self.staff.get(reverse('saleexception-list')).json()['results']]
        self.assertEqual(open_ids, [self.stock.pk])
        done = self.staff.get(reverse('saleexception-list'), {'resolved': 'true'}).json()['results']
        self.assertEqual([r['id'] for r in done], [self.price.pk])
        by_kind = self.staff.get(reverse('saleexception-list'), {'kind': 'stock'}).json()['results']
        self.assertEqual([r['id'] for r in by_kind], [self.stock.pk])
        again = self.staff.post(reverse('saleexception-resolve', args=[self.price.pk]), {}, format='json')
        self.assertEqual(again.status_code, 400)

    def test_staff_only(self):
        self.assertEqual(self.client.get(reverse('saleexception-list')).status_code, 403)  # the cashier
        member = self.as_user(UserFactory(company=self.company))
        self.assertEqual(member.post(reverse('saleexception-resolve', args=[self.stock.pk])).status_code, 403)

    def test_other_company_exception_is_404(self):
        theirs = SaleException.objects.exclude(sale__session__till__branch__company=self.company).get()
        self.assertEqual(self.staff.post(reverse('saleexception-resolve', args=[theirs.pk])).status_code, 404)
