"""Post a date range (G53): ``manage.py post_journals --from 2026-10-01 --to 2026-10-31 [--company 3]``."""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from accounting.postings import run_postings
from tenants.models import Company


class Command(BaseCommand):
    help = 'Post till takings, customer ledger documents, waste and butchery loss for a date range (UK days).'

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='start', required=True, help='YYYY-MM-DD')
        parser.add_argument('--to', dest='end', required=True, help='YYYY-MM-DD')
        parser.add_argument('--company', type=int, help='Only this company id (default: every company).')

    def handle(self, *args, start, end, company=None, **options):
        try:
            start, end = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            raise CommandError('Dates are YYYY-MM-DD.') from None
        if start > end:
            raise CommandError('--from is on or before --to.')
        companies = Company.objects.order_by('pk')
        if company is not None:
            companies = companies.filter(pk=company)
        for each in companies:
            summary = run_postings(each, start, end)
            self.stdout.write(f'Company {each.pk}: {summary["posted"]} posted, {summary["already"]} already, '
                              f'{summary["moved"]} moved to an open month, {summary["skipped"]} skipped, '
                              f'{len(summary["conflicts"])} conflicts{f"; {summary['error']}" if summary["error"] else ""}')
