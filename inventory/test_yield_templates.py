# inventory/test_yield_templates.py

"""G20: butchery yield templates — an input product broken into outputs and waste, expected percentages ≤ 100."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import YieldTemplate, YieldTemplateOutput
from tenants.roles import STOCK, TILL

D = Decimal


def weighed(company, name):
    return ProductFactory(company=company, name=name, price=D('0.00'), stock_quantity=0, sold_by='weight',
                          price_per_kg=D('9.0000'))


class YieldTemplateApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = APIClient()
        self.staff.force_authenticate(UserFactory(company=self.company, is_staff=True))
        self.list_url = reverse('yieldtemplate-list')
        self.side = weighed(self.company, 'Fake Side of Beef')
        self.steak = weighed(self.company, 'Fake Rump Steak')
        self.mince = weighed(self.company, 'Fake Beef Mince')

    def body(self, outputs=None, **fields):
        if outputs is None:
            outputs = [
                {'kind': 'product', 'product': self.steak.pk, 'expected_percent': '30.00'},
                {'kind': 'product', 'product': self.mince.pk, 'expected_percent': '45.50'},
                {'kind': 'waste', 'name': 'Bone', 'expected_percent': '15.00'},
                {'kind': 'waste', 'name': 'Fat and trim', 'expected_percent': '6.50'},
            ]
        return {'name': 'Fake side breakdown', 'input_product': self.side.pk, 'outputs': outputs, **fields}

    def create(self, **kwargs):
        return self.staff.post(self.list_url, self.body(**kwargs), format='json')

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def test_create_shows_expected_yield_waste_and_loss(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body['expected_yield_percent'], '75.50')
        self.assertEqual(body['expected_waste_percent'], '21.50')
        self.assertEqual(body['expected_loss_percent'], '3.00')
        self.assertEqual([o['name'] for o in body['outputs']],
                         ['Fake Rump Steak', 'Fake Beef Mince', 'Bone', 'Fat and trim'])
        self.assertEqual(body['input_product_name'], 'Fake Side of Beef')
        self.assertTrue(body['is_active'])
        template = YieldTemplate.objects.get()
        self.assertEqual(template.company, self.company)
        self.assertEqual(template.expected_loss_percent, D('3.00'))

    def test_exactly_100_percent_has_no_loss(self):
        response = self.create(outputs=[
            {'kind': 'product', 'product': self.steak.pk, 'expected_percent': '60.00'},
            {'kind': 'waste', 'name': 'Bone', 'expected_percent': '40.00'},
        ])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['expected_loss_percent'], '0.00')

    def test_over_100_percent_refused(self):
        response = self.create(outputs=[
            {'kind': 'product', 'product': self.steak.pk, 'expected_percent': '60.00'},
            {'kind': 'waste', 'name': 'Bone', 'expected_percent': '40.01'},
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['outputs'],
                         ['Outputs add up to 100.01%; they can be at most 100%.'])
        self.assertFalse(YieldTemplate.objects.exists())

    def test_percent_must_be_positive_and_at_most_100(self):
        for percent in ('0.00', '-1.00', '100.01', '12.345'):
            with self.subTest(percent=percent):
                response = self.create(outputs=[
                    {'kind': 'product', 'product': self.steak.pk, 'expected_percent': percent}])
                self.assertEqual(response.status_code, 400)

    def test_needs_a_product_output(self):
        for outputs in ([], [{'kind': 'waste', 'name': 'Bone', 'expected_percent': '20.00'}]):
            with self.subTest(outputs=outputs):
                response = self.create(outputs=outputs)
                self.assertEqual(response.status_code, 400)
                self.assertIn('outputs', response.json())

    def test_output_rules(self):
        cases = [
            ({'kind': 'product', 'expected_percent': '10.00'}, 'Choose the output product.'),
            ({'kind': 'waste', 'expected_percent': '10.00'}, 'Name the waste (e.g. Bone).'),
            ({'kind': 'waste', 'name': 'Bone', 'product': self.mince.pk, 'expected_percent': '10.00'},
             'Waste is not a product; leave the product out.'),
            ({'kind': 'product', 'product': self.side.pk, 'expected_percent': '10.00'},
             'Fake Side of Beef is the input; it cannot be an output too.'),
        ]
        for output, message in cases:
            with self.subTest(message=message):
                response = self.create(outputs=[
                    {'kind': 'product', 'product': self.steak.pk, 'expected_percent': '10.00'}, output])
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, str(response.json()['outputs']))

    def test_same_output_twice_refused(self):
        twice = [{'kind': 'product', 'product': self.steak.pk, 'expected_percent': '10.00'}] * 2
        self.assertIn('Fake Rump Steak is listed twice.', str(self.create(outputs=twice).json()['outputs']))
        bones = [{'kind': 'product', 'product': self.steak.pk, 'expected_percent': '10.00'},
                 {'kind': 'waste', 'name': 'Bone', 'expected_percent': '10.00'},
                 {'kind': 'waste', 'name': ' bone ', 'expected_percent': '10.00'}]
        self.assertIn('Bone is listed twice.', str(self.create(outputs=bones).json()['outputs']))

    def test_products_must_be_sold_by_weight(self):
        cake = ProductFactory(company=self.company, name='Fake Cake', price=D('2.00'))
        response = self.create(input_product=cake.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['input_product'], ['Fake Cake is sold by each; a breakdown needs a weighed '
                                                            'product.'])
        response = self.create(outputs=[{'kind': 'product', 'product': cake.pk, 'expected_percent': '10.00'}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Fake Cake is sold by each', str(response.json()['outputs']))

    def test_name_unique_per_company(self):
        self.assertEqual(self.create().status_code, 201)
        response = self.create()
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json())

    def test_update_replaces_outputs_and_patch_without_them_keeps_them(self):
        template_id = self.create().json()['id']
        detail = reverse('yieldtemplate-detail', args=[template_id])
        response = self.staff.patch(detail, {'outputs': [
            {'kind': 'product', 'product': self.mince.pk, 'expected_percent': '80.00'}]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['expected_loss_percent'], '20.00')
        self.assertEqual(YieldTemplateOutput.objects.count(), 1)
        response = self.staff.patch(detail, {'name': 'Fake renamed', 'is_active': False}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['outputs']), 1)
        over = self.staff.patch(detail, {'outputs': [
            {'kind': 'product', 'product': self.mince.pk, 'expected_percent': '80.00'},
            {'kind': 'waste', 'name': 'Bone', 'expected_percent': '30.00'}]}, format='json')
        self.assertEqual(over.status_code, 400)
        self.assertEqual(YieldTemplateOutput.objects.get().expected_percent, D('80.00'))

    def test_other_companys_template_is_404_and_not_listed(self):
        other = CompanyFactory()
        theirs = YieldTemplate.objects.create(company=other, name='Theirs', input_product=weighed(other, 'Their Side'))
        detail = reverse('yieldtemplate-detail', args=[theirs.pk])
        self.assertEqual(self.staff.get(detail).status_code, 404)
        self.assertEqual(self.staff.patch(detail, {'name': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.staff.delete(detail).status_code, 404)
        self.assertEqual(self.staff.get(self.list_url).json()['results'], [])

    def test_sent_company_is_ignored(self):
        response = self.create(company=CompanyFactory().pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(YieldTemplate.objects.get().company, self.company)

    def test_another_companys_products_refused(self):
        other = CompanyFactory()
        self.assertIn('input_product', self.create(input_product=weighed(other, 'Their Side').pk).json())
        response = self.create(outputs=[
            {'kind': 'product', 'product': weighed(other, 'Their Steak').pk, 'expected_percent': '10.00'}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('outputs', response.json())

    def test_members_and_stock_operators_read_only_staff_write(self):
        self.assertEqual(self.create().status_code, 201)
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company, is_staff=False))
        self.assertEqual(member.get(self.list_url).status_code, 200)
        self.assertEqual(member.post(self.list_url, self.body(name='Other'), format='json').status_code, 403)
        stock = self.operator(STOCK)
        self.assertEqual(len(stock.get(self.list_url).json()['results']), 1)
        self.assertEqual(stock.post(self.list_url, self.body(name='Other'), format='json').status_code, 403)
        self.assertEqual(self.operator(TILL).get(self.list_url).status_code, 403)

    def test_input_product_in_use_cannot_be_deleted(self):
        from django.db.models import ProtectedError
        self.assertEqual(self.create().status_code, 201)
        with self.assertRaises(ProtectedError):
            self.side.delete()


class YieldTemplateAdminTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.side = weighed(self.company, 'Fake Side of Beef')
        self.steak = weighed(self.company, 'Fake Rump Steak')
        self.client.force_login(UserFactory(company=self.company, is_staff=True, is_superuser=True))

    def post(self, *outputs):
        # A superuser is not scoped to a company, so the admin asks which one.
        data = {'name': 'Fake admin template', 'company': self.company.pk, 'input_product': self.side.pk, 'is_active': 'on', 'note': '',
                'outputs-TOTAL_FORMS': str(len(outputs)), 'outputs-INITIAL_FORMS': '0',
                'outputs-MIN_NUM_FORMS': '0', 'outputs-MAX_NUM_FORMS': '1000'}
        for index, output in enumerate(outputs):
            data.update({f'outputs-{index}-{key}': value for key, value in {'position': index, **output}.items()})
        return self.client.post(reverse('admin:inventory_yieldtemplate_add'), data)

    def test_admin_refuses_outputs_over_100_percent(self):
        response = self.post({'kind': 'product', 'product': self.steak.pk, 'expected_percent': '70.00'},
                             {'kind': 'waste', 'name': 'Bone', 'expected_percent': '30.01'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Outputs add up to 100.01%; they can be at most 100%.')
        self.assertFalse(YieldTemplate.objects.exists())

    def test_admin_saves_a_valid_template(self):
        response = self.post({'kind': 'product', 'product': self.steak.pk, 'expected_percent': '70.00'},
                             {'kind': 'waste', 'name': 'Bone', 'expected_percent': '25.00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(YieldTemplate.objects.get().expected_loss_percent, D('5.00'))
