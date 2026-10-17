# sync/serializers.py

from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import ShopifyConnection, SyncTask


class SyncTaskSerializer(TenantModelSerializer):
    class Meta:
        model = SyncTask
        fields = '__all__'
        # System-managed: the id is generated and the state and timings are maintained by the task.
        read_only_fields = ['task_id', 'status', 'start_time', 'end_time']


class ShopifyConnectionSerializer(TenantModelSerializer):
    class Meta:
        model = ShopifyConnection
        fields = ['id', 'company', 'shop_domain', 'access_token', 'created_at']
        read_only_fields = ['created_at']
        extra_kwargs = {'access_token': {'write_only': True}}


class LoyaltyLookupSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=32)


class LoyaltyCustomerSerializer(serializers.Serializer):
    """Only what the till shows: no surname, email or phone ever leaves m18."""

    first_name = serializers.CharField()
    points_balance = serializers.IntegerField()
    tier = serializers.CharField()
    points_gbp_value = serializers.DecimalField(max_digits=12, decimal_places=2)
