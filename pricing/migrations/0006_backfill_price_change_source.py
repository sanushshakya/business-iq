# Existing near-expiry markdowns were only recognisable by their reason; give them their source.

from django.db import migrations

DECAY = 'decay_markdown'


def mark_markdowns(apps, schema_editor):
    apps.get_model('pricing', 'PriceChangeLog').objects.filter(reason=DECAY).update(source=DECAY)


def unmark_markdowns(apps, schema_editor):
    apps.get_model('pricing', 'PriceChangeLog').objects.filter(source=DECAY).update(source='manual')


class Migration(migrations.Migration):

    dependencies = [
        ("pricing", "0005_price_change_decisions"),
    ]

    operations = [migrations.RunPython(mark_markdowns, unmark_markdowns)]
