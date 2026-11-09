"""G51 over the API: the statement PDF, sending one by hand, the email log, the switches; roles and tenancy; beat."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from config.celery import app
from tenants.roles import STOCK
from wholesale.models import CustomerEmail, ReminderItem, TradeCustomer
from wholesale.tests.test_statements import StatementCase


class StatementApiTests(StatementCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.month = f'{self.start:%Y-%m}'
        self.pdf_url = reverse('tradecustomer-statement', args=[self.customer.pk])
        self.send_url = reverse('tradecustomer-send-statement', args=[self.customer.pk])
        self.log_url = reverse('customeremail-list')

    def test_pdf_for_a_month(self):
        answer = self.member.get(self.pdf_url, {'month': self.month})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer['Content-Type'], 'application/pdf')
        self.assertIn(f'statement-{self.customer.account_code}-{self.month}.pdf', answer['Content-Disposition'])
        self.assertTrue(answer.content.startswith(b'%PDF'))
        self.assertEqual(self.member.get(self.pdf_url).status_code, 200)  # default: this month so far
        next_month = (self.end + timedelta(days=1)).strftime('%Y-%m')
        for bad in ('2026-13', '2026-1', 'soon', next_month):
            with self.subTest(bad=bad):
                self.assertEqual(self.member.get(self.pdf_url, {'month': bad}).status_code, 400)

    def test_send_by_hand_then_read_the_log(self):
        self.assertEqual(self.member.post(self.send_url, {'month': self.month}, format='json').status_code, 403)
        with self.captureOnCommitCallbacks(execute=True):
            answer = self.api.post(self.send_url, {'month': self.month}, format='json')
        self.assertEqual(answer.status_code, 202)
        self.assertEqual((answer.json()['kind'], answer.json()['status']), ('statement', 'queued'))
        self.assertEqual(len(mail.outbox), 1)
        rows = self.member.get(self.log_url, {'customer': self.customer.pk}).json()['results']
        self.assertEqual([(row['kind'], row['status'], row['period_start'], row['automatic']) for row in rows],
                         [('statement', 'sent', self.start.isoformat(), False)])
        self.assertEqual(self.api.post(self.send_url, {'month': 'soon'}, format='json').status_code, 400)

    def test_the_log_shows_reminder_items_and_filters(self):
        email = CustomerEmail.objects.create(customer=self.customer, kind='reminder', status='sent')
        ReminderItem.objects.create(email=email, invoice=self.first, stage='overdue_7', open_amount='12.24')
        rows = self.member.get(self.log_url, {'kind': 'reminder'}).json()['results']
        self.assertEqual(rows[0]['items'], [{'invoice': self.first.pk, 'number': 'INV-000001',
                                             'stage': 'overdue_7', 'open_amount': '12.24'}])
        self.assertEqual(self.member.get(self.log_url, {'kind': 'statement'}).json()['results'], [])
        self.assertEqual(self.member.get(self.log_url, {'kind': 'other'}).status_code, 400)
        self.assertEqual(self.member.get(self.log_url, {'customer': 'x'}).status_code, 400)
        self.assertEqual(self.api.post(self.log_url, {}, format='json').status_code, 405)

    def test_the_switches_on_the_customer(self):
        url = reverse('tradecustomer-detail', args=[self.customer.pk])
        self.assertEqual(self.api.get(url).json()['statements_by_email'], True)
        self.assertEqual(self.api.patch(url, {'reminders_by_email': False}, format='json').status_code, 200)
        self.assertFalse(TradeCustomer.objects.get(pk=self.customer.pk).reminders_by_email)

    def test_other_companies_and_operators(self):
        CustomerEmail.objects.create(customer=self.customer, kind='reminder')
        admin = APIClient()
        admin.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True, is_superuser=True))
        self.assertEqual(admin.get(self.log_url).json()['results'], [])
        self.assertEqual(admin.get(self.pdf_url).status_code, 404)
        self.assertEqual(admin.post(self.send_url, {'month': self.month}, format='json').status_code, 404)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        for url in (self.log_url, self.pdf_url):
            self.assertEqual(picker.get(url).status_code, 403)

    def test_beat_runs_registered_tasks(self):
        import wholesale.tasks  # noqa: F401 - what the worker's autodiscovery imports

        schedule = app.conf.beat_schedule
        for name in ('send-monthly-statements', 'send-payment-reminders-daily'):
            self.assertIn(schedule[name]['task'], app.tasks)
