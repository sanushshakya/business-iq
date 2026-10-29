# Every batch has a location and every movement a source and destination from here on.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0013_place_existing_stock'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockbatch',
            name='location',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name='batches', to='inventory.location'),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='source_location',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name='movements_out', to='inventory.location'),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='destination_location',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name='movements_in', to='inventory.location'),
        ),
    ]
