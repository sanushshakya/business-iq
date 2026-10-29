from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from inventory.models import Product
from sync.models import StorefrontConnection
from tenants.models import Company

MAX_PRICE = Decimal('999.99')  # m18's price column cap; it refuses 0 < p ≤ 999.99 violations item by item
REPORT_MAX_AGE = timedelta(hours=48)


def _gap(row):
    """m18 minus IQ; a value that is not a finite number counts as no gap."""
    try:
        gap = Decimal(str(row['m18'])) - Decimal(str(row['iq']))
    except (InvalidOperation, KeyError, TypeError):
        return Decimal(0)
    return gap if gap.is_finite() else Decimal(0)


class Command(BaseCommand):
    help = ("Before switching a storefront connection's push_prices on: the price differences in the latest nightly "
            "report, linked products whose price m18 would refuse, and the switch's history. See docs/runbooks/price-push.md.")

    def add_arguments(self, parser):
        parser.add_argument('--company', default='M18-DEMO', help='Registration number of the company (default M18-DEMO).')
        parser.add_argument('--top', type=int, default=10, help='How many of the largest price gaps to list (default 10).')

    def handle(self, *args, **options):
        company = Company.objects.filter(registration_number=options['company']).first()
        if company is None:
            raise CommandError(f"No company with registration number {options['company']!r}.")
        connection = StorefrontConnection.objects.filter(company=company).first()
        if connection is None:
            raise CommandError(f'{company} has no storefront connection.')

        out, blockers = self.stdout.write, []
        if not connection.is_active or not connection.base_url:
            blockers.append('the connection is inactive or has no base URL')
        out(f"{connection} — push_prices: {'on' if connection.push_prices else 'off'}"
            f"{'' if connection.is_active else ' (connection inactive)'}")
        for change in connection.price_push_changes.select_related('changed_by')[:5]:
            out(f"  {change.changed_at:%Y-%m-%d %H:%M} switched {'on' if change.turned_on else 'off'}"
                f" by {change.changed_by or 'a script'}")

        report = connection.reconciliations.first()
        if report is None:
            blockers.append('no nightly report yet')
        else:
            out(f'Latest nightly report: {report.ran_at:%Y-%m-%d %H:%M}, {report.checked} product(s) checked')
            if report.error:
                blockers.append(f'the last report failed: {report.error}')
            elif timezone.now() - report.ran_at > REPORT_MAX_AGE:
                blockers.append('the last report is older than 48 h')
            gaps = sorted((row for row in report.mismatches if row.get('kind') == 'price_different'),
                          key=lambda row: abs(_gap(row)), reverse=True)
            out(f'Price differences: {len(gaps)} (IQ\'s price replaces m18\'s once switched on)')
            for row in gaps[:options['top']]:
                out(f"  {row['product_id']}: IQ {row['iq']}, m18 {row['m18']} ({_gap(row):+})")

        refused = (Product.objects.filter(company=company).exclude(external_id='')
                   .filter(Q(price__lte=0) | Q(price__gt=MAX_PRICE)).order_by('name'))
        out(f'Out-of-range prices (m18 refuses them): {refused.count()}')
        for product in refused:
            out(f'  {product.name} ({product.external_id}): {product.price}')
        if refused:
            blockers.append('fix the out-of-range prices')

        out('Ready: yes' if not blockers else f"Ready: no — {'; '.join(blockers)}")
