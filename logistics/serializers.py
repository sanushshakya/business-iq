# logistics/serializers.py


from rest_framework import serializers

from common.models import FreightRateCache
from common.tenancy import TenantModelSerializer

from .models import AlternativeSupplier, UserSupplier, FreightAlert, LogisticProvider, Delivery


class FreightAlertSerializer(TenantModelSerializer):
    class Meta:
        model = FreightAlert
        fields = '__all__'


class LogisticProviderSerializer(TenantModelSerializer):
    class Meta:
        model = LogisticProvider
        fields = '__all__'


class DeliverySerializer(TenantModelSerializer):
    class Meta:
        model = Delivery
        fields = '__all__'


def _validate_categories(value):
    if not isinstance(value, list) or not all(isinstance(name, str) and name.strip() for name in value):
        raise serializers.ValidationError('Must be a list of category names, e.g. ["Dried fruit"].')
    return [name.strip() for name in value]


class UserSupplierSerializer(TenantModelSerializer):
    class Meta:
        model = UserSupplier
        fields = '__all__'

    validate_product_categories = staticmethod(_validate_categories)


class AlternativeSupplierSerializer(TenantModelSerializer):
    class Meta:
        model = AlternativeSupplier
        fields = '__all__'

    validate_product_categories = staticmethod(_validate_categories)


class FreightRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FreightRateCache
        fields = ['id', 'company', 'service_code', 'rate', 'currency', 'last_updated']
        read_only_fields = fields
