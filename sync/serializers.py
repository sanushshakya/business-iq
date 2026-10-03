# sync/serializers.py


from common.tenancy import TenantModelSerializer

from .models import ShopifyConnection, SyncTask


class SyncTaskSerializer(TenantModelSerializer):
    class Meta:
        model = SyncTask
        fields = '__all__'


class ShopifyConnectionSerializer(TenantModelSerializer):
    class Meta:
        model = ShopifyConnection
        fields = ['id', 'company', 'shop_domain', 'access_token', 'created_at']
        read_only_fields = ['created_at']
        extra_kwargs = {'access_token': {'write_only': True}}
