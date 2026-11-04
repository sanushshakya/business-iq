# inventory/signals.py

"""
New companies and branches get their default locations straight away (``locations.ensure_default_locations``);
a deleted product leaves a ``ProductRemoval`` for till catalogues (G26).
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from tenants.models import Branch, Company

from .models import Product, ProductRemoval


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


@receiver(post_delete, sender=Product, dispatch_uid='inventory.product_removal_marker')
def product_removal_marker(sender, instance, origin=None, **kwargs):
    started_by = getattr(origin, 'model', type(origin))  # an instance or a queryset started the delete
    if started_by is not Company:  # a company being deleted takes its markers with it
        ProductRemoval.objects.create(company_id=instance.company_id, product_id=instance.pk)
