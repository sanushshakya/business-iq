# Place stock that existed before G09 in a location. Quantities are not touched.

from django.db import migrations


def primary_location(apps, company):
    """The oldest branch's shop floor; a company without one gets a sellable MAIN location."""
    Location = apps.get_model('inventory', 'Location')
    floor = (
        Location.objects.filter(company=company, type='shop_floor', branch__isnull=False)
        .order_by('branch_id', 'pk').first()
    )
    if floor is not None:
        return floor
    main, _ = Location.objects.get_or_create(
        company=company, code='MAIN',
        defaults={'name': 'Main stock', 'type': 'warehouse', 'sellable': True, 'online_sellable': True},
    )
    return main


def virtual(apps, company, type_, code, name):
    Location = apps.get_model('inventory', 'Location')
    location, _ = Location.objects.get_or_create(company=company, type=type_, defaults={'code': code, 'name': name})
    return location


def forwards(apps, schema_editor):
    Company = apps.get_model('tenants', 'Company')
    StockBatch = apps.get_model('inventory', 'StockBatch')
    StockMovement = apps.get_model('inventory', 'StockMovement')
    for company in Company.objects.all():
        batches = StockBatch.objects.filter(product__company=company, location__isnull=True)
        if not batches.exists() and not StockMovement.objects.filter(
                batch__product__company=company, source_location__isnull=True).exists():
            continue
        home = primary_location(apps, company)
        supplier = virtual(apps, company, 'supplier', 'SUPPLIER', 'Suppliers')
        customer = virtual(apps, company, 'customer', 'CUSTOMER', 'Customers')
        batches.update(location=home)
        moves = StockMovement.objects.filter(batch__product__company=company, source_location__isnull=True)
        moves.filter(movement_type='inbound').update(source_location=supplier, destination_location=home)
        moves.filter(movement_type='outbound').update(source_location=home, destination_location=customer)


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0012_stock_locations'),
        ('tenants', '0001_initial'),
    ]

    # Reverse: nothing to undo here; reversing 0012 drops the fields (a MAIN location, if made, stays).
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
