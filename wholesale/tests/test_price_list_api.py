"""G39: customer groups, price lists and the resolve endpoint over the API, plus the admin. All names are fake."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import Product
from tenants.roles import STOCK, TILL
from wholesale.models import CustomerGroup, PriceList, PriceListEntry, TradeCustomer

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = APIClient()
        self.staff.force_authenticate(UserFactory(company=self.company, is_staff=True))
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.group = CustomerGroup.objects.create(company=self.company, name='Fake Restaurants')
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd', group=self.group)
        self.each = ProductFactory(company=self.company, wholesale_price=D('5.0000'))
        self.weighed = ProductFactory(company=self.company, sold_by=Product.WEIGHT, price_per_kg=D('12.99'))
        self.other_company = CompanyFactory()
        self.foreign_product = ProductFactory(company=self.other_company)
        self.foreign_customer = TradeCustomer.objects.create(company=self.other_company, account_code='C-1',
                                                             legal_name='E2E Fake Theirs Ltd')
        self.foreign_group = CustomerGroup.objects.create(company=self.other_company, name='Theirs')

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def list_body(self, **fields):
        return {
            'name': 'Restaurants 2026', 'priority': 5, 'valid_from': '2026-11-01', 'valid_to': '2026-12-31',
            'customers': [self.customer.pk], 'groups': [self.group.pk],
            'entries': [
                {'product': self.each.pk, 'min_quantity': '1', 'unit_price': '4.50'},
                {'product': self.each.pk, 'min_quantity': '10', 'unit_price': '4.20'},
                {'product': self.weighed.pk, 'min_quantity': '0.5', 'unit_price': '8.8000'},
            ],
            **fields,
        }

    def create_list(self, client=None, **fields):
        return (client or self.staff).post(reverse('pricelist-list'), self.list_body(**fields), format='json')


class GroupApiTests(Base):
    def test_crud_and_deleting_leaves_customers_without_a_group(self):
        created = self.staff.post(reverse('customergroup-list'), {'name': 'Fake Cafes', 'note': 'x'}, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        group_id = created.json()['id']
        self.assertEqual(CustomerGroup.objects.get(pk=group_id).company, self.company)
        duplicate = self.staff.post(reverse('customergroup-list'), {'name': 'Fake Cafes'}, format='json')
        self.assertEqual(duplicate.status_code, 400)
        names = [g['name'] for g in self.member.get(reverse('customergroup-list')).json()['results']]
        self.assertEqual(names, ['Fake Cafes', 'Fake Restaurants'])
        self.assertEqual(self.staff.delete(reverse('customergroup-detail', args=[self.group.pk])).status_code, 204)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.group)

    def test_members_read_staff_write_operators_refused(self):
        self.assertEqual(self.member.post(reverse('customergroup-list'), {'name': 'X'}, format='json').status_code,
                         403)
        for role in (TILL, STOCK):
            self.assertEqual(self.operator(role).get(reverse('customergroup-list')).status_code, 403)

    def test_another_companys_group_is_a_404(self):
        response = self.staff.get(reverse('customergroup-detail', args=[self.foreign_group.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.staff.get(reverse('customergroup-list')).json()['count'], 1)

    def test_customer_takes_a_group_of_its_company_only(self):
        url = reverse('tradecustomer-detail', args=[self.customer.pk])
        self.assertEqual(self.staff.get(url).json()['group'], self.group.pk)
        self.assertEqual(self.staff.patch(url, {'group': self.foreign_group.pk}, format='json').status_code, 400)
        self.assertEqual(self.staff.patch(url, {'group': None}, format='json').json()['group'], None)


class PriceListApiTests(Base):
    def test_create_with_entries(self):
        response = self.create_list()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data['customers'], [self.customer.pk])
        self.assertEqual(data['groups'], [self.group.pk])
        self.assertEqual([(e['product'], e['min_quantity'], e['unit_price']) for e in data['entries']], [
            (self.each.pk, '1.000', '4.5000'), (self.each.pk, '10.000', '4.2000'),
            (self.weighed.pk, '0.500', '8.8000'),
        ])
        self.assertEqual(data['entries'][0]['product_name'], self.each.name)
        price_list = PriceList.objects.get(pk=data['id'])
        self.assertEqual(price_list.company, self.company)
        self.assertEqual(price_list.entries.count(), 3)

    def test_sending_entries_replaces_them_leaving_them_out_keeps_them(self):
        list_id = self.create_list().json()['id']
        url = reverse('pricelist-detail', args=[list_id])
        self.assertEqual(self.staff.patch(url, {'priority': 7}, format='json').status_code, 200)
        self.assertEqual(PriceListEntry.objects.filter(price_list_id=list_id).count(), 3)
        response = self.staff.patch(url, {'entries': [
            {'product': self.each.pk, 'min_quantity': '2', 'unit_price': '4.00'}]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([e['min_quantity'] for e in response.json()['entries']], ['2.000'])

    def test_members_read_staff_write_operators_refused(self):
        list_id = self.create_list().json()['id']
        self.assertEqual(self.member.get(reverse('pricelist-detail', args=[list_id])).status_code, 200)
        self.assertEqual(self.create_list(self.member, name='Other').status_code, 403)
        self.assertEqual(self.member.delete(reverse('pricelist-detail', args=[list_id])).status_code, 403)
        for role in (TILL, STOCK):
            api = self.operator(role)
            self.assertEqual(api.get(reverse('pricelist-list')).status_code, 403)
            self.assertEqual(api.post(reverse('price-resolve'), {}, format='json').status_code, 403)

    def test_tenancy(self):
        theirs = PriceList.objects.create(company=self.other_company, name='Theirs')
        self.assertEqual(self.staff.get(reverse('pricelist-detail', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.staff.get(reverse('pricelist-list')).json()['count'], 0)
        for fields in ({'customers': [self.foreign_customer.pk]}, {'groups': [self.foreign_group.pk]},
                       {'entries': [{'product': self.foreign_product.pk, 'unit_price': '1'}]}):
            with self.subTest(fields=list(fields)):
                self.assertEqual(self.create_list(**fields).status_code, 400)
        response = self.create_list(company=self.other_company.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(PriceList.objects.get(pk=response.json()['id']).company, self.company)

    def test_name_unique_per_company(self):
        PriceList.objects.create(company=self.other_company, name='Restaurants 2026')
        self.assertEqual(self.create_list().status_code, 201)
        self.assertEqual(self.create_list().status_code, 400)

    def test_bad_values(self):
        cases = {
            'valid_to': {'valid_from': '2026-12-01', 'valid_to': '2026-11-30'},
            'priority': {'priority': 1001},
            'entries': {'entries': [{'product': self.each.pk, 'min_quantity': '1', 'unit_price': '1'},
                                    {'product': self.each.pk, 'min_quantity': '1.000', 'unit_price': '2'}]},
        }
        entry_cases = [
            {'product': self.each.pk, 'min_quantity': '1.5', 'unit_price': '1'},  # each: whole units
            {'product': self.each.pk, 'min_quantity': '0', 'unit_price': '1'},
            {'product': self.weighed.pk, 'min_quantity': '0.0004', 'unit_price': '1'},  # kg to 3 dp
            {'product': self.each.pk, 'min_quantity': '1', 'unit_price': '-0.01'},
            {'product': self.each.pk, 'min_quantity': '1', 'unit_price': '1.00001'},  # 4 dp
        ]
        for field, fields in cases.items():
            with self.subTest(field):
                response = self.create_list(**fields)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        for entry in entry_cases:
            with self.subTest(entry=entry):
                self.assertEqual(self.create_list(entries=[entry]).status_code, 400)
        self.assertFalse(PriceList.objects.exists())

    def test_a_draft_with_no_one_assigned_is_allowed(self):
        response = self.create_list(customers=[], groups=[], entries=[], valid_from=None, valid_to=None)
        self.assertEqual(response.status_code, 201, response.content)

    def test_filters(self):
        listed = self.create_list().json()['id']
        PriceList.objects.create(company=self.company, name='Off', is_active=False)

        def ids(**params):
            return [p['id'] for p in self.member.get(reverse('pricelist-list'), params).json()['results']]

        self.assertEqual(ids(active='true'), [listed])
        self.assertEqual(len(ids(active='false')), 1)
        self.assertEqual(ids(customer=self.customer.pk), [listed])
        self.assertEqual(ids(group=self.group.pk), [listed])
        self.assertEqual(ids(product=self.weighed.pk), [listed])
        self.assertEqual(ids(customer=self.foreign_customer.pk), [])
        for bad in ({'active': 'maybe'}, {'customer': 'x'}, {'product': '1.5'}):
            with self.subTest(bad=bad):
                self.assertEqual(self.member.get(reverse('pricelist-list'), bad).status_code, 400)

    def test_delete(self):
        list_id = self.create_list().json()['id']
        self.assertEqual(self.staff.delete(reverse('pricelist-detail', args=[list_id])).status_code, 204)
        self.assertFalse(PriceListEntry.objects.exists())


class ResolveApiTests(Base):
    url = reverse('price-resolve')

    def resolve(self, lines, client=None, **fields):
        return (client or self.member).post(self.url, {'customer': self.customer.pk, 'on': '2026-11-06',
                                                      'lines': lines, **fields}, format='json')

    def test_each_source_and_no_price(self):
        mine = PriceList.objects.create(company=self.company, name='Mine')
        mine.customers.add(self.customer)
        PriceListEntry.objects.create(price_list=mine, product=self.each, min_quantity=10, unit_price=D('4.2'))
        theirs = PriceList.objects.create(company=self.company, name='Group')
        theirs.groups.add(self.group)
        PriceListEntry.objects.create(price_list=theirs, product=self.weighed, min_quantity=D('0.5'),
                                      unit_price=D('8.8'))
        response = self.resolve([
            {'product': self.each.pk, 'quantity': '12'},
            {'product': self.weighed.pk, 'quantity': '0.750'},
            {'product': self.each.pk, 'quantity': '2'},
            {'product': self.weighed.pk, 'quantity': '0.400'},
        ])
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()['lines']
        self.assertEqual(rows[0], {'product': self.each.pk, 'quantity': '12.000', 'unit_price': '4.2000',
                                   'source': 'customer_list', 'price_list': mine.pk, 'price_list_name': 'Mine',
                                   'min_quantity': '10.000'})
        self.assertEqual((rows[1]['source'], rows[1]['unit_price'], rows[1]['price_list']),
                         ('group_list', '8.8000', theirs.pk))
        self.assertEqual((rows[2]['source'], rows[2]['unit_price'], rows[2]['price_list']), ('base', '5.0000', None))
        self.assertEqual(rows[3], {'product': self.weighed.pk, 'quantity': '0.400', 'error': 'no_price'})
        self.assertEqual(response.json()['customer'], self.customer.pk)
        self.assertEqual(response.json()['on'], '2026-11-06')

    def test_refusals(self):
        line = {'product': self.each.pk, 'quantity': '1'}
        self.assertEqual(self.resolve([line], customer=self.foreign_customer.pk).status_code, 400)
        self.assertEqual(self.resolve([{'product': self.foreign_product.pk, 'quantity': '1'}]).status_code, 400)
        self.assertEqual(self.resolve([]).status_code, 400)
        self.assertEqual(self.resolve([line] * 501).status_code, 400)
        self.assertEqual(self.resolve([line] * 500).status_code, 200)
        for quantity in ('0', '-1', '1.5', 'x'):
            with self.subTest(quantity=quantity):
                self.assertEqual(self.resolve([{'product': self.each.pk, 'quantity': quantity}]).status_code, 400)
        self.assertEqual(self.resolve([{'product': self.weighed.pk, 'quantity': '0.0005'}]).status_code, 400)

    def test_on_defaults_to_today(self):
        response = self.member.post(self.url, {'customer': self.customer.pk,
                                               'lines': [{'product': self.each.pk, 'quantity': '1'}]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['lines'][0]['source'], 'base')


class WholesalePriceFieldTests(Base):
    def test_staff_set_and_see_it_others_do_not(self):
        url = reverse('product-detail', args=[self.each.pk])
        response = self.staff.patch(url, {'wholesale_price': '4.7500'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['wholesale_price'], '4.7500')
        self.assertEqual(self.staff.patch(url, {'wholesale_price': '-1'}, format='json').status_code, 400)
        self.assertNotIn('wholesale_price', self.member.get(url).json())
        self.assertNotIn('wholesale_price', self.operator(TILL).get(url).json())

    def test_not_on_the_till_catalogue(self):
        response = self.operator(TILL).get(reverse('till-catalogue'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['products'])
        self.assertTrue(all('wholesale_price' not in p for p in response.json()['products']))


class PriceListAdminTests(Base):
    def setUp(self):
        super().setUp()
        staff = UserFactory(company=self.company, is_staff=True)
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label__in=['wholesale', 'inventory']))
        self.client.force_login(staff)
        self.mine = PriceList.objects.create(company=self.company, name='Mine')
        self.theirs = PriceList.objects.create(company=self.other_company, name='Theirs')

    def test_scoped_lists_and_choices(self):
        response = self.client.get(reverse('admin:wholesale_pricelist_changelist'))
        self.assertContains(response, 'Mine')
        self.assertNotContains(response, 'Theirs')
        self.assertEqual(self.client.get(reverse('admin:wholesale_pricelist_change',
                                                 args=[self.theirs.pk])).status_code, 302)
        page = self.client.get(reverse('admin:wholesale_pricelist_change', args=[self.mine.pk]))
        self.assertEqual(page.status_code, 200)
        form = page.context['adminform'].form
        self.assertEqual(list(form.fields['customers'].queryset), [self.customer])
        self.assertEqual(list(form.fields['groups'].queryset), [self.group])
        groups = self.client.get(reverse('admin:wholesale_customergroup_changelist'))
        self.assertContains(groups, 'Fake Restaurants')
        self.assertNotContains(groups, 'Theirs')

    def test_add_a_list_with_an_entry(self):
        prefix = 'entries'
        response = self.client.post(reverse('admin:wholesale_pricelist_add'), {
            'name': 'Admin list', 'priority': '0', 'is_active': 'on', 'customers': [self.customer.pk],
            f'{prefix}-TOTAL_FORMS': '1', f'{prefix}-INITIAL_FORMS': '0',
            f'{prefix}-0-product': self.weighed.pk, f'{prefix}-0-min_quantity': '0.25',
            f'{prefix}-0-unit_price': '8.5',
        })
        self.assertEqual(response.status_code, 302, response.content[:2000])
        created = PriceList.objects.get(name='Admin list')
        self.assertEqual(created.company, self.company)
        self.assertEqual(created.entries.get().min_quantity, D('0.250'))

    def test_admin_refuses_a_whole_unit_break_in_kg_terms_for_each(self):
        prefix = 'entries'
        response = self.client.post(reverse('admin:wholesale_pricelist_add'), {
            'name': 'Bad list', 'priority': '0',
            f'{prefix}-TOTAL_FORMS': '1', f'{prefix}-INITIAL_FORMS': '0',
            f'{prefix}-0-product': self.each.pk, f'{prefix}-0-min_quantity': '1.5',
            f'{prefix}-0-unit_price': '1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PriceList.objects.filter(name='Bad list').exists())

    def test_product_admin_shows_the_wholesale_price(self):
        page = self.client.get(reverse('admin:inventory_product_change', args=[self.each.pk]))
        self.assertContains(page, 'name="wholesale_price"')
