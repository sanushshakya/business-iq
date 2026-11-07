"""G38: trade customers (B2B accounts) — tenant-scoped, account codes unique per company, nested addresses and
contacts. All names, emails and numbers here are fake."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK, TILL
from wholesale.models import TradeCustomer, TradeCustomerAddress, TradeCustomerContact


def body(**fields):
    return {
        'account_code': 'rest-001',
        'legal_name': 'E2E Fake Restaurant Ltd',
        'trading_name': 'Fake Bistro',
        'vat_number': 'gb 123 4567 89',
        'delivery_days': ['fri', 'mon', 'wed'],
        'addresses': [
            {'label': 'Kitchen door', 'line1': '1 Fake Street', 'town': 'Testtown', 'postcode': 'tt1 1tt',
             'instructions': 'Ring the bell', 'is_default': True},
            {'label': 'Second site', 'line1': '2 Fake Road', 'town': 'Testtown', 'postcode': 'TT2 2TT'},
        ],
        'contacts': [
            {'name': 'Test Chef', 'role': 'Head chef', 'email': 'chef@example.test', 'phone': '01000 000000',
             'is_primary': True},
            {'name': 'Test Accounts', 'role': 'Accounts', 'email': 'accounts@example.test'},
        ],
        **fields,
    }


class TradeCustomerApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = APIClient()
        self.staff.force_authenticate(UserFactory(company=self.company, is_staff=True))
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.list_url = reverse('tradecustomer-list')

    def create(self, client=None, **fields):
        return (client or self.staff).post(self.list_url, body(**fields), format='json')

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def test_create_with_addresses_and_contacts(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data['account_code'], 'REST-001')
        self.assertEqual(data['vat_number'], 'GB123456789')
        self.assertEqual(data['delivery_days'], ['mon', 'wed', 'fri'])  # week order
        self.assertEqual(data['status'], 'active')
        self.assertEqual([a['label'] for a in data['addresses']], ['Kitchen door', 'Second site'])
        self.assertEqual(data['addresses'][0]['postcode'], 'TT1 1TT')
        self.assertEqual([c['is_primary'] for c in data['contacts']], [True, False])
        customer = TradeCustomer.objects.get(pk=data['id'])
        self.assertEqual(customer.company, self.company)
        self.assertEqual(customer.addresses.count(), 2)

    def test_members_read_staff_write(self):
        customer_id = self.create().json()['id']
        self.assertEqual(self.member.get(self.list_url).json()['count'], 1)
        self.assertEqual(self.member.get(reverse('tradecustomer-detail', args=[customer_id])).status_code, 200)
        self.assertEqual(self.create(self.member, account_code='X-2').status_code, 403)
        patch = self.member.patch(reverse('tradecustomer-detail', args=[customer_id]), {'status': 'closed'},
                                  format='json')
        self.assertEqual(patch.status_code, 403)

    def test_operators_are_refused(self):
        self.create()
        for role in (TILL, STOCK):
            self.assertEqual(self.operator(role).get(self.list_url).status_code, 403, role)

    def test_another_company_cannot_see_or_change_it(self):
        customer_id = self.create().json()['id']
        other = APIClient()
        other.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True))
        detail = reverse('tradecustomer-detail', args=[customer_id])
        self.assertEqual(other.get(self.list_url).json()['count'], 0)
        self.assertEqual(other.get(detail).status_code, 404)
        self.assertEqual(other.patch(detail, {'status': 'closed'}, format='json').status_code, 404)
        self.assertEqual(TradeCustomer.objects.get(pk=customer_id).status, 'active')

    def test_company_in_the_body_is_ignored(self):
        other = CompanyFactory()
        response = self.create(company=other.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TradeCustomer.objects.get(pk=response.json()['id']).company, self.company)

    def test_account_code_is_unique_per_company(self):
        self.create()
        duplicate = self.create(account_code='REST-001')
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('account_code', duplicate.json())
        # Another company may use the same code.
        other = APIClient()
        other_company = CompanyFactory()
        other.force_authenticate(UserFactory(company=other_company, is_staff=True))
        self.assertEqual(self.create(other).status_code, 201)

    def test_update_replaces_addresses_and_contacts_only_when_sent(self):
        customer_id = self.create().json()['id']
        detail = reverse('tradecustomer-detail', args=[customer_id])
        self.assertEqual(self.staff.patch(detail, {'status': 'on_hold'}, format='json').status_code, 200)
        self.assertEqual(TradeCustomerAddress.objects.filter(customer_id=customer_id).count(), 2)
        response = self.staff.patch(detail, {'contacts': [{'name': 'New Fake Contact', 'is_primary': True}]},
                                    format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([c['name'] for c in response.json()['contacts']], ['New Fake Contact'])
        self.assertEqual(TradeCustomerContact.objects.filter(customer_id=customer_id).count(), 1)
        self.assertEqual(TradeCustomerAddress.objects.filter(customer_id=customer_id).count(), 2)

    def test_filters(self):
        self.create()
        self.create(account_code='CAFE-9', legal_name='E2E Fake Cafe Ltd', trading_name='', status='closed')
        self.assertEqual(self.staff.get(self.list_url, {'status': 'closed'}).json()['count'], 1)
        self.assertEqual(self.staff.get(self.list_url, {'search': 'cafe'}).json()['count'], 1)
        self.assertEqual(self.staff.get(self.list_url, {'search': 'bistro'}).json()['count'], 1)
        self.assertEqual(self.staff.get(self.list_url, {'search': 'rest-0'}).json()['count'], 1)
        self.assertEqual(self.staff.get(self.list_url, {'status': 'nonsense'}).status_code, 400)

    def test_no_delete(self):
        customer_id = self.create().json()['id']
        response = self.staff.delete(reverse('tradecustomer-detail', args=[customer_id]))
        self.assertEqual(response.status_code, 405)  # close it instead: orders will point at it

    def test_bad_values(self):
        cases = [
            ({'account_code': 'has space'}, 'account_code'),
            ({'account_code': ''}, 'account_code'),
            ({'account_code': 'A' * 21}, 'account_code'),
            ({'vat_number': 'GB12345'}, 'vat_number'),
            ({'vat_number': 'FR12345678901'}, 'vat_number'),
            ({'delivery_days': ['mon', 'funday']}, 'delivery_days'),
            ({'delivery_days': ['mon', 'mon']}, 'delivery_days'),
            ({'status': 'gone'}, 'status'),
            ({'legal_name': ''}, 'legal_name'),
            ({'addresses': [{'label': 'A', 'line1': '1 Fake St', 'town': 'T', 'postcode': 'TT1 1TT',
                             'is_default': True},
                            {'label': 'B', 'line1': '2 Fake St', 'town': 'T', 'postcode': 'TT1 1TT',
                             'is_default': True}]}, 'addresses'),
            ({'addresses': [{'label': 'A', 'line1': '', 'town': 'T', 'postcode': 'TT1 1TT'}]}, 'addresses'),
            ({'addresses': [{'label': 'A', 'line1': '1 Fake St', 'town': 'T', 'postcode': 'NOT A CODE'}]},
             'addresses'),
            ({'contacts': [{'name': 'A', 'is_primary': True}, {'name': 'B', 'is_primary': True}]}, 'contacts'),
            ({'contacts': [{'name': 'A', 'email': 'not-an-email'}]}, 'contacts'),
            ({'contacts': [{'name': ''}]}, 'contacts'),
        ]
        for fields, field in cases:
            response = self.create(**fields)
            self.assertEqual(response.status_code, 400, fields)
            self.assertIn(field, response.json(), fields)
        self.assertFalse(TradeCustomer.objects.exists())

    def test_vat_number_is_optional_and_xi_is_allowed(self):
        self.assertEqual(self.create(vat_number='').json()['vat_number'], '')
        self.assertEqual(self.create(account_code='NI-1', vat_number='XI 123 4567 89 012').json()['vat_number'],
                         'XI123456789012')

    def test_a_single_address_becomes_the_default(self):
        response = self.create(addresses=[{'label': 'Only', 'line1': '1 Fake St', 'town': 'T', 'postcode': 'TT1 1TT'}],
                               contacts=[{'name': 'Only Fake Contact'}])
        self.assertTrue(response.json()['addresses'][0]['is_default'])
        self.assertTrue(response.json()['contacts'][0]['is_primary'])

    def test_form_encoded_create(self):
        response = self.staff.post(self.list_url, {'account_code': 'form-1', 'legal_name': 'E2E Fake Form Ltd',
                                                   'vat_number': 'gb 123 456 789'})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['account_code'], response.json()['vat_number']), ('FORM-1', 'GB123456789'))

    def test_no_addresses_or_contacts_is_fine(self):
        response = self.create(addresses=[], contacts=[])
        self.assertEqual(response.status_code, 201, response.content)


class TradeCustomerAdminTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        staff = UserFactory(company=self.company, is_staff=True)
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label='wholesale'))
        self.client.force_login(staff)
        self.mine = TradeCustomer.objects.create(company=self.company, account_code='MINE-1',
                                                 legal_name='E2E Fake Mine Ltd')
        self.theirs = TradeCustomer.objects.create(company=CompanyFactory(), account_code='THEIRS-1',
                                                   legal_name='E2E Fake Theirs Ltd')

    def test_staff_see_only_their_company(self):
        response = self.client.get(reverse('admin:wholesale_tradecustomer_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MINE-1')
        self.assertNotContains(response, 'THEIRS-1')
        change = reverse('admin:wholesale_tradecustomer_change', args=[self.theirs.pk])
        self.assertIn(self.client.get(change).status_code, (302, 404))

    def test_admin_adds_a_customer_with_an_address(self):
        response = self.client.post(reverse('admin:wholesale_tradecustomer_add'), {
            'account_code': 'adm-1', 'legal_name': 'E2E Fake Admin Ltd', 'payment_terms_days': '30', 'trading_name': '', 'vat_number': '',
            'status': 'active', 'delivery_days': '["tue"]', 'note': '',
            'addresses-TOTAL_FORMS': '1', 'addresses-INITIAL_FORMS': '0',
            'addresses-0-label': 'Door', 'addresses-0-line1': '1 Fake St', 'addresses-0-line2': '',
            'addresses-0-town': 'Testtown', 'addresses-0-postcode': 'TT1 1TT', 'addresses-0-instructions': '',
            'addresses-0-is_default': 'on',
            'contacts-TOTAL_FORMS': '0', 'contacts-INITIAL_FORMS': '0',
        })
        self.assertEqual(response.status_code, 302, response.content[:2000])
        customer = TradeCustomer.objects.get(account_code='ADM-1')
        self.assertEqual(customer.company, self.company)
        self.assertEqual(customer.addresses.get().postcode, 'TT1 1TT')

    def test_admin_refuses_a_duplicate_code_cleanly(self):
        response = self.client.post(reverse('admin:wholesale_tradecustomer_add'), {
            'account_code': 'mine-1', 'legal_name': 'E2E Fake Copy Ltd', 'payment_terms_days': '30', 'trading_name': '', 'vat_number': '',
            'status': 'active', 'delivery_days': '[]', 'note': '',
            'addresses-TOTAL_FORMS': '0', 'addresses-INITIAL_FORMS': '0',
            'contacts-TOTAL_FORMS': '0', 'contacts-INITIAL_FORMS': '0',
        })
        self.assertEqual(response.status_code, 200)  # the form again, with the error
        self.assertContains(response, 'Another customer already has this account code.')
        self.assertEqual(TradeCustomer.objects.filter(account_code='MINE-1').count(), 1)


class ReviewFollowUpTests(TestCase):
    """The G38 review's cases: one-save default switch, lengths before normalising, scoping, races, superusers."""

    def setUp(self):
        self.company = CompanyFactory()
        self.staff_user = UserFactory(company=self.company, is_staff=True)
        self.staff_user.user_permissions.set(Permission.objects.filter(content_type__app_label='wholesale'))
        self.client.force_login(self.staff_user)
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='MINE-1',
                                                     legal_name='E2E Fake Mine Ltd')
        self.a = TradeCustomerAddress.objects.create(customer=self.customer, label='A', line1='1 Fake St', town='T',
                                                     postcode='TT1 1TT', is_default=True)
        self.b = TradeCustomerAddress.objects.create(customer=self.customer, label='B', line1='2 Fake St', town='T',
                                                     postcode='TT2 2TT')
        self.api = APIClient()
        self.api.force_authenticate(self.staff_user)

    def change_post(self, a_default, b_default, **extra):
        data = {
            'account_code': 'MINE-1', 'legal_name': 'E2E Fake Mine Ltd', 'payment_terms_days': '30', 'trading_name': '', 'vat_number': '',
            'status': 'active', 'delivery_days': '[]', 'note': '',
            'addresses-TOTAL_FORMS': '2', 'addresses-INITIAL_FORMS': '2',
            'contacts-TOTAL_FORMS': '0', 'contacts-INITIAL_FORMS': '0',
        }
        for i, (address, default) in enumerate(((self.a, a_default), (self.b, b_default))):
            data.update({f'addresses-{i}-id': address.pk, f'addresses-{i}-customer': self.customer.pk,
                         f'addresses-{i}-label': address.label, f'addresses-{i}-line1': address.line1,
                         f'addresses-{i}-line2': '', f'addresses-{i}-town': address.town,
                         f'addresses-{i}-postcode': address.postcode, f'addresses-{i}-instructions': ''})
            if default:
                data[f'addresses-{i}-is_default'] = 'on'
        data.update(extra)
        return self.client.post(reverse('admin:wholesale_tradecustomer_change', args=[self.customer.pk]), data)

    def test_admin_moves_the_default_in_one_save(self):
        response = self.change_post(False, True)
        self.assertEqual(response.status_code, 302, response.content[:3000])
        self.assertEqual(TradeCustomerAddress.objects.get(is_default=True), self.b)

    def test_admin_refuses_two_defaults(self):
        response = self.change_post(True, True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only one default address.')

    def test_admin_normalises_before_length_checks(self):
        response = self.change_post(True, False, vat_number='GB 123 456 789 012', **{'addresses-1-postcode': 'tt2  2tt'})
        self.assertEqual(response.status_code, 302, response.content[:3000])
        self.customer.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.customer.vat_number, self.b.postcode), ('GB123456789012', 'TT2 2TT'))

    def test_admin_address_and_contact_pages_are_scoped(self):
        theirs = TradeCustomer.objects.create(company=CompanyFactory(), account_code='T-1', legal_name='E2E Fake T')
        address = TradeCustomerAddress.objects.create(customer=theirs, label='X', line1='9 Fake St', town='T',
                                                      postcode='TT9 9TT')
        contact = TradeCustomerContact.objects.create(customer=theirs, name='E2E Fake Person')
        for url in (reverse('admin:wholesale_tradecustomeraddress_change', args=[address.pk]),
                    reverse('admin:wholesale_tradecustomercontact_change', args=[contact.pk])):
            self.assertIn(self.client.get(url).status_code, (302, 404), url)

    def test_superuser_duplicate_check_uses_the_chosen_company(self):
        superuser = UserFactory(company=self.company, is_staff=True, is_superuser=True)
        self.client.force_login(superuser)
        other = CompanyFactory()
        response = self.client.post(reverse('admin:wholesale_tradecustomer_add'), {
            'company': other.pk, 'account_code': 'MINE-1', 'legal_name': 'E2E Fake Other Ltd', 'payment_terms_days': '30', 'trading_name': '',
            'vat_number': '', 'status': 'active', 'delivery_days': '[]', 'note': '',
            'addresses-TOTAL_FORMS': '0', 'addresses-INITIAL_FORMS': '0',
            'contacts-TOTAL_FORMS': '0', 'contacts-INITIAL_FORMS': '0',
        })
        self.assertEqual(response.status_code, 302, response.content[:3000])
        self.assertTrue(TradeCustomer.objects.filter(company=other, account_code='MINE-1').exists())

    def test_api_postcode_with_extra_spaces(self):
        detail = reverse('tradecustomer-detail', args=[self.customer.pk])
        response = self.api.patch(detail, {'addresses': [{'label': 'C', 'line1': '3 Fake St', 'town': 'T',
                                                          'postcode': 'sw1a  1aa'}]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['addresses'][0]['postcode'], 'SW1A 1AA')

    def test_patch_with_two_defaults_changes_nothing(self):
        detail = reverse('tradecustomer-detail', args=[self.customer.pk])
        two = [{'label': n, 'line1': '1 Fake St', 'town': 'T', 'postcode': 'TT1 1TT', 'is_default': True}
               for n in 'XY']
        self.assertEqual(self.api.patch(detail, {'addresses': two}, format='json').status_code, 400)
        self.assertEqual(sorted(self.customer.addresses.values_list('label', flat=True)), ['A', 'B'])

    def test_a_lost_race_on_the_code_is_a_400_on_the_code(self):
        from unittest import mock

        from common.tenancy import TenantModelSerializer

        with mock.patch.object(TenantModelSerializer, '_check_company_unique_constraints', lambda *a, **k: None):
            response = self.api.post(reverse('tradecustomer-list'), {'account_code': 'mine-1',
                                                                      'legal_name': 'E2E Fake Race Ltd'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.json()), ['account_code'])

    def test_contact_names_stay_out_of_admin_history(self):
        contact = TradeCustomerContact.objects.create(customer=self.customer, name='E2E Fake Person', role='Accounts')
        self.assertEqual(str(contact), 'Accounts')
        nameless = TradeCustomerContact.objects.create(customer=self.customer, name='E2E Fake Other')
        self.assertEqual(str(nameless), f'Contact #{nameless.pk}')
