"""G58: promotions over the API — the rules per kind, status and end, tenancy and roles."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import ProductCategory
from promotions.models import Promotion
from promotions.services import active_promotions
from tenants.models import Branch
from tenants.roles import TILL

TODAY = date(2026, 6, 10)
D = Decimal


@mock.patch('promotions.models.uk_date', lambda: TODAY)
@mock.patch('promotions.serializers.uk_date', lambda: TODAY)
@mock.patch('promotions.views.uk_date', lambda: TODAY)
class PromotionApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.branch = Branch.objects.create(company=self.company, name='Fake High St', address='1 Fake St')
        self.cola = ProductFactory(company=self.company, name='Fake Cola', price=D('1.20'))
        self.crisps = ProductFactory(company=self.company, name='Fake Crisps', price=D('1.00'))
        self.wrap = ProductFactory(company=self.company, name='Fake Wrap', price=D('2.50'))
        self.mince = ProductFactory(company=self.company, name='Fake Mince', sold_by='weight',
                                    price_per_kg=D('8.99'))
        self.category = ProductCategory.objects.create(name=f'Fake drinks {self.company.pk}')
        self.url = reverse('promotion-list')

    def body(self, **fields):
        return {'name': '3 for £3', 'kind': 'multi_buy', 'starts_on': '2026-06-01', 'buy_quantity': 3,
                'deal_price': '3.00', 'products': [self.cola.pk], **fields}

    def create(self, **fields):
        return self.api.post(self.url, self.body(**fields), format='json')

    def test_each_kind_valid(self):
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create(kind='bogof', name='BOGOF', buy_quantity=1, get_quantity=1,
                                     deal_price=None).status_code, 201)
        meal = self.create(kind='meal_deal', name='Meal deal', buy_quantity=None, products=[], deal_price='3.50',
                           groups=[{'name': 'Main', 'products': [self.wrap.pk]},
                                   {'name': 'Snack', 'products': [self.crisps.pk]},
                                   {'name': 'Drink', 'products': [self.cola.pk]}])
        self.assertEqual(meal.status_code, 201, meal.content)
        self.assertEqual([g['name'] for g in meal.json()['groups']], ['Main', 'Snack', 'Drink'])
        percent = self.create(kind='category_percent', name='10% off drinks', buy_quantity=None, deal_price=None,
                              products=[], percent='10.00', categories=[self.category.pk], stacks=True,
                              branches=[self.branch.pk])
        self.assertEqual(percent.status_code, 201, percent.content)
        self.assertEqual(percent.json()['status'], 'live')

    def test_kind_rules(self):
        bad = [
            self.body(buy_quantity=1),  # a multi-buy set is 2 or more
            self.body(deal_price='0.00'),
            self.body(products=[]),
            self.body(percent='10.00'),  # not a field of a multi-buy
            self.body(stacks=True),
            self.body(products=[self.mince.pk]),  # weighed products only take % off
            self.body(kind='bogof', buy_quantity=1, get_quantity=None, deal_price=None),
            self.body(kind='bogof', buy_quantity=60, get_quantity=60, deal_price=None),
            self.body(kind='meal_deal', buy_quantity=None, products=[], deal_price='3.50',
                      groups=[{'name': 'Main', 'products': [self.wrap.pk]}]),  # at least 2 groups
            self.body(kind='meal_deal', buy_quantity=None, products=[], deal_price='3.50',
                      groups=[{'name': 'Main', 'products': [self.wrap.pk]},
                              {'name': 'Drink', 'products': [self.wrap.pk]}]),  # a product in one group only
            self.body(kind='meal_deal', buy_quantity=None, products=[], deal_price='3.50',
                      groups=[{'name': 'Main', 'products': [self.wrap.pk]}, {'name': 'Drink', 'products': []}]),
            self.body(kind='category_percent', buy_quantity=None, deal_price=None, products=[], percent='0.00',
                      categories=[self.category.pk]),
            self.body(kind='category_percent', buy_quantity=None, deal_price=None, products=[], percent='100.01',
                      categories=[self.category.pk]),
            self.body(kind='category_percent', buy_quantity=None, deal_price=None, products=[], percent='10.00'),
            self.body(ends_on='2026-05-31'),  # before it starts
            self.body(priority=1001),
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.api.post(self.url, body, format='json').status_code, 400)
        self.assertFalse(Promotion.objects.exists())

    def test_status_filter_and_end(self):
        live = self.create().json()
        self.create(name='Later', starts_on='2026-07-01')
        self.create(name='Gone', starts_on='2026-05-01', ends_on='2026-05-31')
        names = lambda status: [p['name'] for p in self.member.get(self.url, {'status': status}).json()['results']]  # noqa: E731
        self.assertEqual((names('live'), names('scheduled'), names('ended')), (['3 for £3'], ['Later'], ['Gone']))
        end = reverse('promotion-end', args=[live['id']])
        self.assertEqual(self.member.post(end).status_code, 403)
        ended = self.api.post(end)
        self.assertEqual((ended.status_code, ended.json()['status'], ended.json()['ends_on']),
                         (200, 'ended', '2026-06-10'))
        self.assertEqual(self.api.post(end).status_code, 400)  # already ended
        self.assertEqual(self.api.delete(reverse('promotion-detail', args=[live['id']])).status_code, 405)

    def test_edit_replaces_groups(self):
        meal = self.create(kind='meal_deal', name='Meal deal', buy_quantity=None, products=[], deal_price='3.50',
                           groups=[{'name': 'Main', 'products': [self.wrap.pk]},
                                   {'name': 'Drink', 'products': [self.cola.pk]}]).json()
        edited = self.api.patch(reverse('promotion-detail', args=[meal['id']]),
                                {'groups': [{'name': 'Main', 'products': [self.wrap.pk]},
                                            {'name': 'Snack', 'products': [self.crisps.pk]},
                                            {'name': 'Drink', 'products': [self.cola.pk]}]}, format='json')
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(len(edited.json()['groups']), 3)
        self.assertEqual(self.api.patch(reverse('promotion-detail', args=[meal['id']]), {'percent': '5.00'},
                                        format='json').status_code, 400)  # still a meal deal

    def test_tenancy_and_roles(self):
        mine = self.create().json()
        other = CompanyFactory()
        theirs = ProductFactory(company=other)
        their_branch = Branch.objects.create(company=other, name='Fake Other', address='-')
        self.assertEqual(self.create(products=[theirs.pk]).status_code, 400)
        self.assertEqual(self.create(branches=[their_branch.pk]).status_code, 400)
        made = self.create(company=other.pk).json()
        self.assertEqual(Promotion.objects.get(pk=made['id']).company, self.company)  # company is ignored
        outsider = APIClient()
        outsider.force_authenticate(UserFactory(company=other, is_staff=True))
        self.assertEqual(outsider.get(reverse('promotion-detail', args=[mine['id']])).status_code, 404)
        self.assertEqual(outsider.get(self.url).json()['count'], 0)
        self.assertEqual(self.member.post(self.url, self.body(), format='json').status_code, 403)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Cashier', TILL)
        till = APIClient()
        till.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(till.get(self.url).status_code, 403)

    def test_superuser_cannot_use_another_companys_products(self):
        boss = get_user_model().objects.create_superuser(email='fake-root@example.com', password='x-123456789',
                                                         company=self.company)
        api = APIClient()
        api.force_authenticate(boss)
        theirs = ProductFactory(company=CompanyFactory())
        self.assertEqual(api.post(self.url, self.body(products=[theirs.pk]), format='json').status_code, 400)


class ActivePromotionsTests(TestCase):
    def test_in_force_for_the_branch_and_day(self):
        company = CompanyFactory()
        here = Branch.objects.create(company=company, name='Fake Here', address='-')
        there = Branch.objects.create(company=company, name='Fake There', address='-')
        cola = ProductFactory(company=company)

        def make(name, **fields):
            promotion = Promotion.objects.create(name=name, kind='multi_buy', buy_quantity=2, deal_price=D('2.00'),
                                                 **{'company': company, 'starts_on': TODAY, **fields})
            promotion.products.add(cola)
            return promotion

        make('Everywhere')
        make('Here only').branches.add(here)
        make('There only').branches.add(there)
        make('Tomorrow', starts_on=TODAY + timedelta(days=1))
        make('Yesterday', starts_on=TODAY - timedelta(days=5), ends_on=TODAY - timedelta(days=1))
        make('Ends today', starts_on=TODAY - timedelta(days=5), ends_on=TODAY)
        make('Off', is_active=False)
        make('Elsewhere', company=CompanyFactory())
        found = active_promotions(company.pk, here.pk, TODAY)
        self.assertEqual(sorted(p.name for p in found), ['Ends today', 'Everywhere', 'Here only'])
        self.assertEqual(found[0].products, frozenset({cola.pk}))
