"""G47: post a ledger entry for every invoice and credit note issued before the ledger existed (idempotent)."""

from django.db import migrations

# Plain managers and raw deletes: the model's own guards refuse deletes (also when this runs with the real models).


def backfill(apps, schema_editor):
    Invoice = apps.get_model('wholesale', 'Invoice')
    CreditNote = apps.get_model('wholesale', 'CreditNote')
    LedgerEntry = apps.get_model('wholesale', 'LedgerEntry')
    entries = LedgerEntry._base_manager
    rows = []
    for invoice in Invoice._base_manager.filter(status='issued', ledger_entry__isnull=True, gross_total__gt=0) \
            .select_related('order'):
        rows.append(LedgerEntry(customer_id=invoice.order.customer_id, kind='invoice', entry_date=invoice.invoice_date,
                                amount=invoice.gross_total, invoice=invoice,
                                description=f'Invoice INV-{invoice.number:06d}', created_by_id=invoice.issued_by_id))
    for note in CreditNote._base_manager.filter(status='issued', ledger_entry__isnull=True, gross_total__gt=0) \
            .select_related('invoice__order'):
        rows.append(LedgerEntry(customer_id=note.invoice.order.customer_id, kind='credit_note',
                                entry_date=note.credit_date, amount=-note.gross_total, credit_note=note,
                                description=f'Credit note CN-{note.number:06d}', created_by_id=note.issued_by_id))
    entries.bulk_create(rows)


def unpost(apps, schema_editor):
    queryset = apps.get_model('wholesale', 'LedgerEntry')._base_manager.filter(kind__in=['invoice', 'credit_note'])
    queryset._raw_delete(queryset.db)


class Migration(migrations.Migration):
    dependencies = [('wholesale', '0010_customer_ledger')]

    operations = [migrations.RunPython(backfill, unpost)]
