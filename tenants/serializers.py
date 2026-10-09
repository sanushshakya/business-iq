# tenants/serializers.py

from rest_framework import serializers

from common.tenancy import TenantModelSerializer

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
