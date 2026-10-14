# tenants/serializers.py

from django.contrib.auth import get_user_model
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .roles import ROLE_LABELS

from .models import Branch, Company, Till


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['id', 'name', 'registration_number', 'address', 'created_at']
        # The registration number identifies the company; only the admin site changes it.
        read_only_fields = ['id', 'registration_number', 'created_at']


class BranchSerializer(TenantModelSerializer):
    class Meta:
        model = Branch
        fields = '__all__'
        read_only_fields = ['created_at']


class TillSerializer(TenantModelSerializer):
    class Meta:
        model = Till
        fields = '__all__'
        read_only_fields = ['created_at']


class OperatorSerializer(serializers.ModelSerializer):
    """An operator as an admin sees them. The code is never here: it is shown once, on create and reset."""

    role_label = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ['id', 'name', 'role', 'role_label', 'is_active', 'created_at', 'last_login']
        read_only_fields = ['id', 'created_at', 'last_login']
        extra_kwargs = {'name': {'required': True, 'allow_blank': False}, 'role': {'required': True}}

    def get_role_label(self, obj) -> str:
        return ROLE_LABELS.get(obj.role, '')

    def validate_role(self, value):
        if not value:
            raise serializers.ValidationError('Choose a role.')
        return value
