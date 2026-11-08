"""G49: issued credit notes pay their own invoice (up to what is open there), as issuing one does from now on."""

from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def allocate(apps, schema_editor):
    LedgerEntry = apps.get_model('wholesale', 'LedgerEntry')
    Allocation = apps.get_model('wholesale', 'Allocation')
    allocations = Allocation._base_manager

    def active(**side):
        return allocations.filter(released_at__isnull=True, **side).aggregate(total=Sum('amount'))['total'] \
            or Decimal('0')

    for note_entry in LedgerEntry._base_manager.filter(kind='credit_note').select_related('credit_note') \
            .order_by('id'):
        if allocations.filter(credit=note_entry).exists():
            continue  # already allocated (by issuing, or an earlier run)
        invoice_entry = LedgerEntry._base_manager.filter(invoice_id=note_entry.credit_note.invoice_id).first()
        if invoice_entry is None:
            continue
        amount = min(invoice_entry.amount - active(debit=invoice_entry), -note_entry.amount)
        if amount > 0:
            allocations.create(credit=note_entry, debit=invoice_entry, amount=amount, created_by=None)


def unallocate(apps, schema_editor):
    # Only what this made (no user); raw: the model's own guards refuse deletes.
    queryset = apps.get_model('wholesale', 'Allocation')._base_manager.filter(credit__kind='credit_note',
                                                                             created_by__isnull=True)
    queryset._raw_delete(queryset.db)


class Migration(migrations.Migration):
    dependencies = [('wholesale', '0014_payments')]

    operations = [migrations.RunPython(allocate, unallocate)]
