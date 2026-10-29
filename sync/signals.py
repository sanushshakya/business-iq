# sync/signals.py

"""Mark storefront-linked products for an online stock update whenever their stock or buffer changes."""

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from inventory.models import Location, Product, StockBatch, StockMovement

from .stock import mark_changed, mark_location_changed


@receiver(post_save, sender=StockMovement)
def movement_saved(sender, instance, created, **kwargs):
    if created:
        mark_changed(instance.batch.product_id)


@receiver(post_save, sender=StockBatch)
def batch_saved(sender, instance, **kwargs):
    mark_changed(instance.product_id)


@receiver(post_delete, sender=StockBatch)
def batch_deleted(sender, instance, **kwargs):
    # After commit: the product itself may be being deleted in this transaction.
    product_id = instance.product_id
    transaction.on_commit(lambda: mark_changed(product_id))


@receiver(post_save, sender=Product)
def product_saved(sender, instance, **kwargs):
    mark_changed(instance.pk)


@receiver(post_save, sender=Location)
def location_saved(sender, instance, created, raw=False, **kwargs):
    # Turning online_sellable or is_active on or off changes what is offered online for everything stocked there.
    if not created and not raw:
        mark_location_changed(instance.pk)
