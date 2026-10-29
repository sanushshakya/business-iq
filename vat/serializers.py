from rest_framework import serializers

from .models import VatRate


class VatRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = VatRate
        fields = ['id', 'code', 'rate', 'valid_from']
        read_only_fields = fields
