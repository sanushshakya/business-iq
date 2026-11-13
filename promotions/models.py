"""
Till promotions (G58): multi-buy (3 for £5), buy X get Y free, meal deals and % off categories, for a date range and
some or all branches. ``promotions/engine.py`` decides what a basket gets; the till runs a port of it offline.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from vat.services import uk_date

from .engine import BOGOF, CATEGORY_PERCENT, MEAL_DEAL, MULTI_BUY


class Promotion(models.Model):
    KINDS = [(MULTI_BUY, 'Multi-buy'), (BOGOF, 'Buy X get Y free'), (MEAL_DEAL, 'Meal deal'),
             (CATEGORY_PERCENT, '% off a category')]
    SCHEDULED, LIVE, ENDED = 'scheduled', 'live', 'ended'

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='promotions')
    name = models.CharField(max_length=80, help_text='Printed on the receipt.')
    kind = models.CharField(max_length=20, choices=KINDS)
    priority = models.IntegerField(default=0, validators=[MinValueValidator(-1000), MaxValueValidator(1000)],
                                   help_text='Higher goes first; an item is used by one promotion only.')
    starts_on = models.DateField(help_text='UK date, inclusive.')
    ends_on = models.DateField(null=True, blank=True, help_text='UK date, inclusive; blank = no end.')
    is_active = models.BooleanField(default=True)
    stacks = models.BooleanField(default=False, help_text='% off only: also applies on top of other promotions.')
    buy_quantity = models.PositiveSmallIntegerField(null=True, blank=True,
                                                    help_text='Multi-buy: items in a set; BOGOF: items paid.')
    get_quantity = models.PositiveSmallIntegerField(null=True, blank=True, help_text='BOGOF: items free.')
    deal_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                     help_text='Multi-buy and meal deal: the price of a set, VAT included.')
    percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    products = models.ManyToManyField('inventory.Product', blank=True, related_name='promotions')
    categories = models.ManyToManyField('inventory.ProductCategory', blank=True, related_name='promotions')
    branches = models.ManyToManyField('tenants.Branch', blank=True, related_name='promotions',
                                      help_text='None chosen: every branch.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-starts_on', '-id']
        constraints = [
            models.CheckConstraint(condition=Q(ends_on__isnull=True) | Q(ends_on__gte=F('starts_on')),
                                   name='promotion_ends_after_start'),
            models.CheckConstraint(condition=Q(priority__gte=-1000, priority__lte=1000), name='promotion_priority'),
        ]

    def __str__(self):
        return self.name

    def status(self, today=None):
        today = today or uk_date()
        if not self.is_active or (self.ends_on is not None and self.ends_on < today):
            return self.ENDED
        return self.SCHEDULED if self.starts_on > today else self.LIVE


class PromotionGroup(models.Model):
    """One choice of a meal deal (main, snack, drink): one item from each group makes a deal."""

    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name='groups')
    name = models.CharField(max_length=40)
    position = models.PositiveSmallIntegerField(default=0)
    products = models.ManyToManyField('inventory.Product', related_name='+')

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return self.name
