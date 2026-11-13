"""G58: the promotions in force, as the engine (and the till's catalogue) takes them."""

from datetime import timedelta

from django.db.models import Q

from .engine import EnginePromotion
from .models import Promotion

CATALOGUE_AHEAD = timedelta(days=7)  # a till offline across a start date still has the promotion


def _with_parts(queryset):
    return queryset.prefetch_related('products', 'categories', 'branches', 'groups__products').order_by('id')


def to_engine(promotion):
    return EnginePromotion(
        id=promotion.pk, name=promotion.name, kind=promotion.kind, priority=promotion.priority,
        stacks=promotion.stacks, buy_quantity=promotion.buy_quantity, get_quantity=promotion.get_quantity,
        deal_price=promotion.deal_price, percent=promotion.percent,
        products=frozenset(p.pk for p in promotion.products.all()),
        categories=frozenset(c.pk for c in promotion.categories.all()),
        groups=tuple(frozenset(p.pk for p in group.products.all()) for group in promotion.groups.all()),
    )


def active_promotions(company_id, branch_id, day):
    """``[EnginePromotion]`` in force at ``branch_id`` on the UK date ``day``."""
    queryset = _with_parts(Promotion.objects.filter(
        Q(ends_on__isnull=True) | Q(ends_on__gte=day), company_id=company_id, is_active=True, starts_on__lte=day,
    ).filter(Q(branches__isnull=True) | Q(branches=branch_id)).distinct())
    return [to_engine(promotion) for promotion in queryset]


def catalogue_promotions(company_id, today):
    """What the till keeps: active promotions not ended by ``today`` and starting within a week (G26 catalogue)."""
    return _with_parts(Promotion.objects.filter(
        Q(ends_on__isnull=True) | Q(ends_on__gte=today), company_id=company_id, is_active=True,
        starts_on__lte=today + CATALOGUE_AHEAD,
    ))
