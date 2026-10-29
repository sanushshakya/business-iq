# inventory/signals.py

"""New companies and branches get their default locations straight away (``locations.ensure_default_locations``)."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from tenants.models import Branch, Company


@receiver(post_save, sender=Company, dispatch_uid='inventory.company_default_locations')
def company_default_locations(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from .locations import ensure_default_locations

        ensure_default_locations(instance)


@receiver(post_save, sender=Branch, dispatch_uid='inventory.branch_default_locations')
def branch_default_locations(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from .locations import ensure_default_locations

        # Only this branch's shop floor: one deleted on purpose elsewhere must not come back.
        ensure_default_locations(instance.company, branches=[instance])
