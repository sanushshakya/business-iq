from django.db import transaction
from rest_framework import serializers

from common.tenancy import TenantModelSerializer
from inventory.models import Product
from vat.services import uk_date

from .engine import BOGOF, CATEGORY_PERCENT, MEAL_DEAL, MULTI_BUY
from .models import Promotion, PromotionGroup

# Which optional fields each kind needs; every other one must be left empty.
NEEDS = {
    MULTI_BUY: {'buy_quantity', 'deal_price', 'products'},
    BOGOF: {'buy_quantity', 'get_quantity', 'products'},
    MEAL_DEAL: {'deal_price', 'groups'},
    CATEGORY_PERCENT: {'percent', 'categories'},
}
OPTIONAL = {'buy_quantity', 'get_quantity', 'deal_price', 'percent', 'products', 'categories', 'groups'}
MAX_SET = 99


class PromotionGroupSerializer(serializers.ModelSerializer):
    products = serializers.PrimaryKeyRelatedField(many=True, queryset=Product.objects.all())

    class Meta:
        model = PromotionGroup
        fields = ['id', 'name', 'products']
        read_only_fields = ['id']


class PromotionSerializer(TenantModelSerializer):
    """A promotion; ``groups`` (meal deals) are replaced when sent. Prices include VAT."""

    groups = PromotionGroupSerializer(many=True, required=False)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = ['id', 'name', 'kind', 'priority', 'starts_on', 'ends_on', 'is_active', 'stacks', 'buy_quantity',
                  'get_quantity', 'deal_price', 'percent', 'products', 'categories', 'branches', 'groups', 'status',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'created_at', 'updated_at']

    def get_status(self, promotion) -> str:
        return promotion.status(uk_date())

    def get_fields(self):
        fields = super().get_fields()
        if isinstance(self.instance, Promotion):
            fields['kind'].read_only = True  # changing the kind is a new promotion
        return fields

    def _value(self, attrs, name):
        if name in attrs:
            return attrs[name]
        if self.instance is None:
            return None
        if name == 'groups':
            return [{'products': list(g.products.all())} for g in self.instance.groups.all()]
        if name in ('products', 'categories', 'branches'):
            return list(getattr(self.instance, name).all())
        return getattr(self.instance, name)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        get = lambda name: self._value(attrs, name)  # noqa: E731
        kind = get('kind')
        needs, errors = NEEDS[kind], {}
        for name in sorted(OPTIONAL):
            value = get(name)
            empty = value in (None, '') or value == []
            if name in needs and empty:
                errors[name] = 'Needed for this kind of promotion.'
            elif name not in needs and not empty:
                errors[name] = 'Not used by this kind of promotion; leave it empty.'
        if get('stacks') and kind != CATEGORY_PERCENT:
            errors['stacks'] = 'Only a % off promotion can stack.'
        if errors:
            raise serializers.ValidationError(errors)

        if kind == MULTI_BUY and not 2 <= get('buy_quantity') <= MAX_SET:
            errors['buy_quantity'] = f'A set is 2 to {MAX_SET} items.'
        if kind == BOGOF and (get('buy_quantity') < 1 or get('get_quantity') < 1
                              or get('buy_quantity') + get('get_quantity') > MAX_SET):
            errors['buy_quantity'] = f'Buy at least 1 and get at least 1 free, {MAX_SET} items at most.'
        if kind in (MULTI_BUY, MEAL_DEAL) and get('deal_price') <= 0:
            errors['deal_price'] = 'The deal price must be above 0.'
        if kind == CATEGORY_PERCENT and not 0 < get('percent') <= 100:
            errors['percent'] = 'Between 0.01 and 100.'
        if kind == MEAL_DEAL:
            groups = get('groups')
            products = [p for g in groups for p in g['products']]
            if not 2 <= len(groups) <= 5:
                errors['groups'] = 'A meal deal has 2 to 5 groups.'
            elif any(not g['products'] for g in groups):
                errors['groups'] = 'Each group needs at least one product.'
            elif len({p.pk for p in products}) != len(products):
                errors['groups'] = 'A product can be in one group of a meal deal only.'
        else:
            products = get('products') or []
        if kind != CATEGORY_PERCENT:
            weighed = [p.name for p in products if p.is_weighed]
            if weighed:
                errors['products'] = f'Sold by weight (only % off applies): {", ".join(weighed)}.'
        company_id = self._company_id_for(attrs)
        # Superusers are not scoped by TenantModelSerializer: check the company of everything referenced.
        if any(p.company_id != company_id for p in products):
            errors['products'] = "Only your company's products."
        if any(b.company_id != company_id for b in get('branches') or []):
            errors['branches'] = "Only your company's branches."
        starts, ends = get('starts_on'), get('ends_on')
        if starts and ends and ends < starts:
            errors['ends_on'] = 'The end is before the start.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        groups = validated_data.pop('groups', None)
        promotion = super().create(validated_data)
        self._set_groups(promotion, groups)
        return promotion

    @transaction.atomic
    def update(self, instance, validated_data):
        groups = validated_data.pop('groups', None)
        promotion = super().update(instance, validated_data)
        self._set_groups(promotion, groups)
        return promotion

    def _set_groups(self, promotion, groups):
        if groups is None:
            return
        promotion.groups.all().delete()
        for position, group in enumerate(groups):
            made = PromotionGroup.objects.create(promotion=promotion, name=group['name'], position=position)
            made.products.set(group['products'])
