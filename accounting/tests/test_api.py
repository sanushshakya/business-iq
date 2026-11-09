"""G52 over the API: accounts, manual journals and reversals, periods; roles and tenancy."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import Account, Journal
from accounting.tests.test_journal import JournalCase
from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK

D = Decimal


class AccountingApiTests(JournalCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.other_company = CompanyFactory()
        self.stranger = APIClient()
        self.stranger.force_authenticate(UserFactory(company=self.other_company, is_staff=True, is_superuser=True))

    def journal_body(self, **fields):
        return {'date': '2026-06-15', 'narrative': 'Fake opening cash', 'lines': [
            {'account': self.bank.pk, 'debit': '50.00'},
            {'account': self.account('capital').pk, 'credit': '50.00', 'description': 'Fake'}], **fields}

    # Accounts
    def test_accounts_list_create_rename_and_guards(self):
        url = reverse('account-list')
        listed = self.member.get(url, {'page_size': 100}).json()
        rows = listed['results'] if isinstance(listed, dict) else listed
        self.assertIn({'code': '4000', 'system_key': 'sales_standard', 'type': 'income'},
                      [{key: row[key] for key in ('code', 'system_key', 'type')} for row in rows])
        made = self.api.post(url, {'code': '6100', 'name': 'Fake fuel', 'type': 'expense',
                                   'company': self.other_company.pk, 'system_key': 'bank'}, format='json')
        self.assertEqual(made.status_code, 201)
        account = Account.objects.get(pk=made.json()['id'])
        self.assertEqual((account.company, account.system_key), (self.company, None))
        self.assertEqual(self.api.post(url, {'code': '6100', 'name': 'Fake', 'type': 'expense'},
                                       format='json').status_code, 400)
        self.assertEqual(self.api.post(url, {'code': '61', 'name': 'Fake', 'type': 'expense'},
                                       format='json').status_code, 400)
        self.assertEqual(self.member.post(url, {'code': '6200', 'name': 'Fake', 'type': 'expense'},
                                          format='json').status_code, 403)
        detail = reverse('account-detail', args=[self.sales.pk])
        self.assertEqual(self.api.patch(detail, {'name': 'Fake food sales'}, format='json').status_code, 200)
        self.assertEqual(self.api.patch(detail, {'is_active': False}, format='json').status_code, 400)  # system
        self.assertEqual(self.api.delete(detail).status_code, 405)
        self.post()  # now it has lines
        for change in ({'code': '4021'}, {'type': 'expense'}):
            with self.subTest(change=change):
                self.assertEqual(self.api.patch(detail, change, format='json').status_code, 400)
        mine = reverse('account-detail', args=[account.pk])
        self.assertEqual(self.api.patch(mine, {'code': '6101', 'is_active': False}, format='json').status_code, 200)

    # Journals
    def test_post_a_manual_journal_and_read_it_back(self):
        made = self.api.post(reverse('journal-list'), self.journal_body(), format='json')
        self.assertEqual(made.status_code, 201)
        data = made.json()
        self.assertEqual((data['number'], data['source'], data['total']), (1, 'manual', '50.00'))
        self.assertEqual([(line['account_code'], line['debit'], line['credit']) for line in data['lines']],
                         [('1000', '50.00', '0.00'), ('3000', '0.00', '50.00')])
        self.assertEqual(self.member.get(reverse('journal-detail', args=[data['id']])).json()['narrative'],
                         'Fake opening cash')
        listed = self.member.get(reverse('journal-list'), {'from': '2026-06-01', 'to': '2026-06-30',
                                                           'account': self.bank.pk}).json()['results']
        self.assertEqual([row['number'] for row in listed], [1])
        self.assertEqual(self.member.get(reverse('journal-list'), {'from': '2026-07-01'}).json()['results'], [])
        self.assertEqual(self.member.get(reverse('journal-list'), {'from': 'soon'}).status_code, 400)

    def test_bad_journals_are_400(self):
        theirs = Account.objects.get(company=self.other_company, system_key='capital')
        for body in (
            self.journal_body(lines=[{'account': self.bank.pk, 'debit': '50.00'},
                                     {'account': self.account('capital').pk, 'credit': '49.99'}]),
            self.journal_body(lines=[{'account': self.bank.pk, 'debit': '50.00'},
                                     {'account': theirs.pk, 'credit': '50.00'}]),
            self.journal_body(lines=[{'account': self.bank.pk, 'debit': '50.001'},
                                     {'account': self.account('capital').pk, 'credit': '50.001'}]),
            self.journal_body(narrative=''),
            self.journal_body(date='2026-6-1'),
        ):
            with self.subTest(body=body):
                self.assertEqual(self.api.post(reverse('journal-list'), body, format='json').status_code, 400)
        self.assertEqual(self.member.post(reverse('journal-list'), self.journal_body(), format='json').status_code,
                         403)
        self.assertFalse(Journal.objects.exists())

    def test_reverse_and_never_edit(self):
        journal = self.post()
        url = reverse('journal-reverse', args=[journal.pk])
        self.assertEqual(self.member.post(url, {'reason': 'Fake'}, format='json').status_code, 403)
        self.assertEqual(self.api.post(url, {}, format='json').status_code, 400)
        done = self.api.post(url, {'reason': 'Fake typo', 'date': '2026-06-16'}, format='json')
        self.assertEqual(done.status_code, 201)
        self.assertEqual((done.json()['reverses'], done.json()['date']), (journal.pk, '2026-06-16'))
        self.assertEqual(self.api.post(url, {'reason': 'Again'}, format='json').status_code, 400)
        detail = reverse('journal-detail', args=[journal.pk])
        self.assertEqual(self.member.get(detail).json()['reversed_by'], done.json()['id'])
        for method in ('put', 'patch', 'delete'):
            self.assertEqual(getattr(self.api, method)(detail, {}, format='json').status_code, 405)

    # Periods
    def test_lock_and_reopen(self):
        self.post(day=date(2026, 5, 3))
        lock = self.api.post(reverse('period-lock'), {'month': '2026-06'}, format='json')
        self.assertEqual(lock.status_code, 200)
        self.assertEqual(lock.json()['locked_through'], '2026-06-01')
        periods = self.member.get(reverse('period-list')).json()
        rows = periods['results'] if isinstance(periods, dict) else periods
        self.assertEqual([(row['start'], row['locked']) for row in rows], [('2026-05-01', True), ('2026-06-01', True)])
        self.assertEqual(self.api.post(reverse('journal-list'), self.journal_body(), format='json').status_code, 400)
        self.assertEqual(self.api.post(reverse('period-reopen'), {'month': '2026-06'}, format='json').status_code, 400)
        reopen = self.api.post(reverse('period-reopen'), {'month': '2026-06', 'reason': 'Fake late bill'},
                               format='json')
        self.assertEqual((reopen.status_code, reopen.json()['locked_through']), (200, '2026-05-01'))
        self.assertEqual(self.member.post(reverse('period-lock'), {'month': '2026-06'}, format='json').status_code,
                         403)
        self.assertEqual(self.api.post(reverse('period-lock'), {'month': 'June'}, format='json').status_code, 400)

    # Tenancy and roles
    def test_other_companies_and_operators(self):
        journal = self.post()
        self.assertEqual(self.stranger.get(reverse('journal-detail', args=[journal.pk])).status_code, 404)
        self.assertEqual(self.stranger.get(reverse('journal-list')).json()['results'], [])
        self.assertEqual(self.stranger.get(reverse('account-detail', args=[self.bank.pk])).status_code, 404)
        self.assertEqual(self.stranger.post(reverse('journal-reverse', args=[journal.pk]), {'reason': 'Fake'},
                                            format='json').status_code, 404)
        self.stranger.post(reverse('period-lock'), {'month': '2026-06'}, format='json')
        self.assertEqual(self.post(day=date(2026, 6, 20)).number, 2)  # their lock is theirs
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        for url in (reverse('account-list'), reverse('journal-list'), reverse('period-list')):
            self.assertEqual(picker.get(url).status_code, 403)
        self.assertEqual(APIClient().get(reverse('journal-list')).status_code, 401)
