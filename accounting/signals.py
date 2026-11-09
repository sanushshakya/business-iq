"""New companies get the default chart of accounts straight away (as they get default locations)."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from tenants.models import Company


@receiver(post_save, sender=Company, dispatch_uid='accounting.company_default_chart')
def company_default_chart(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from .chart import ensure_default_chart

        ensure_default_chart(instance)
