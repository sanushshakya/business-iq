"""G49: credit adjustments posted before G49 pay the oldest open debits, as posting one does from now on (before
G49 the credit check let every credit pay the oldest debits; this keeps those customers clear)."""

from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def allocate(apps, schema_editor):
    LedgerEntry = apps.get_model('wholesale', 'LedgerEntry')
    Allocation = apps.get_model('wholesale', 'Allocation')
    entries, allocations = LedgerEntry._base_manager, Allocation._base_manager

    def active(**side):
        return allocations.filter(released_at__isnull=True, **side).aggregate(total=Sum('amount'))['total'] \
            or Decimal('0')

    for credit in entries.filter(kind='adjustment', amount__lt=0).order_by('customer_id', 'entry_date', 'id'):
        left = -credit.amount - active(credit=credit)
        if left <= 0:
            continue  # already allocated (by posting, or an earlier run)
        debits = entries.filter(customer_id=credit.customer_id, amount__gt=0, kind__in=['invoice', 'adjustment']) \
            .order_by('entry_date', 'id')
        for debit in debits:
            if left <= 0:
                break
            take = min(left, debit.amount - active(debit=debit))
            if take > 0:
                allocations.create(credit=credit, debit=debit, amount=take, created_by=None)
                left -= take


def unallocate(apps, schema_editor):
    # Only what this made (no user); raw: the model's own guards refuse deletes.
    queryset = apps.get_model('wholesale', 'Allocation')._base_manager.filter(credit__kind='adjustment',
                                                                             created_by__isnull=True)
    queryset._raw_delete(queryset.db)


class Migration(migrations.Migration):
    dependencies = [('wholesale', '0015_allocate_credit_notes')]

    operations = [migrations.RunPython(allocate, unallocate)]
