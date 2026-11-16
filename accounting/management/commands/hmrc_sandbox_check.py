"""
G56's check against HMRC's sandbox, run by hand once the company is connected (Accounts → Connect to HMRC with a
sandbox test organisation): HMRC's verdict on the fraud-prevention headers, the open obligations and, with
``--submit <period key>``, a real sandbox submission of that period's return. The browser fields are fixed values
labelled as this command; tokens are never printed.

    python manage.py hmrc_sandbox_check --company <id> --user <staff user id> [--submit 18A1]
"""

import json
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory

from accounting.hmrc import client, filing, fraud
from tenants.models import Company

CLIENT = {'device_id': '6f2b8a3e-58c1-4b8e-9a51-0c1d2e3f4a5b', 'user_agent': 'hmrc_sandbox_check (manage.py)',
          'screens': [{'width': 1920, 'height': 1080, 'scaling_factor': '1', 'colour_depth': 24}],
          'window': {'width': 1280, 'height': 800}, 'timezone': 'UTC+00:00', 'web_version': 'command'}


class Command(BaseCommand):
    help = "Check the fraud-prevention headers, list obligations and optionally submit, in HMRC's sandbox."

    def add_arguments(self, parser):
        parser.add_argument('--company', type=int, required=True)
        parser.add_argument('--user', type=int, required=True, help='The staff member the submission is made as.')
        parser.add_argument('--submit', metavar='PERIOD_KEY', help="Submit this open obligation's return.")

    def handle(self, *args, **options):
        if settings.HMRC_VAT_ENVIRONMENT != 'sandbox':
            raise CommandError('This check runs only against the HMRC sandbox (HMRC_VAT_ENVIRONMENT=sandbox).')
        company = Company.objects.get(pk=options['company'])
        user = get_user_model().objects.filter(pk=options['user'], company=company, is_staff=True).first()
        if user is None:
            raise CommandError('--user must be a staff member of the company.')
        info = fraud.ClientInfoSerializer(data=CLIENT)
        info.is_valid(raise_exception=True)
        headers = fraud.headers(RequestFactory().post('/'), info.validated_data, user)
        try:
            connection = filing.connection_for(company)
            verdict = client.call(connection, 'GET', '/test/fraud-prevention-headers/validate', fraud=headers)
            self.stdout.write('Fraud-prevention headers: ' + json.dumps(client.json_of(verdict), indent=2))
            rows = filing.obligations(connection, headers, status='O')
        except (filing.FilingError, client.HmrcError) as error:
            raise CommandError(getattr(error, 'detail', None) or f'{error.code}: {error.message}') from None
        self.stdout.write('Open obligations:')
        for row in rows:
            self.stdout.write(f"  {row['periodKey']}  {row['start']} to {row['end']}  due {row['due']}")
        if not options['submit']:
            return
        row = next((r for r in rows if r['periodKey'] == options['submit']), None)
        if row is None:
            raise CommandError(f"{options['submit']} is not an open obligation.")
        try:
            submission = filing.submit(company, user, row['periodKey'], date.fromisoformat(row['start']),
                                       date.fromisoformat(row['end']), headers)
        except filing.FilingError as error:
            raise CommandError(error.detail) from None
        self.stdout.write(f'Submission {submission.pk}: {submission.status}')
        for field in ('form_bundle_number', 'processing_date', 'charge_ref_number', 'payment_indicator',
                      'error_code', 'error_message'):
            value = getattr(submission, field)
            if value:
                self.stdout.write(f'  {field}: {value}')
