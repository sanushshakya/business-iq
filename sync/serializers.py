# sync/serializers.py

from rest_framework import serializers

from .models import ShopifyConnection, SyncTask


class SyncTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncTask
        fields = '__all__'


class ShopifyConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopifyConnection
        fields = ['id', 'company', 'shop_domain', 'access_token', 'created_at']
        read_only_fields = ['created_at']
        extra_kwargs = {'access_token': {'write_only': True}}
