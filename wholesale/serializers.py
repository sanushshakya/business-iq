from django.db import transaction
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import (
    WEEKDAYS,
    TradeCustomer,
    TradeCustomerAddress,
    TradeCustomerContact,
    account_code_problem,
    delivery_days_problem,
    in_week_order,
    normalise_account_code,
    normalise_postcode,
    normalise_vat_number,
    postcode_problem,
    vat_number_problem,
)


class TradeCustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = TradeCustomerAddress
        fields = ['id', 'label', 'line1', 'line2', 'town', 'postcode', 'instructions', 'is_default']
        read_only_fields = ['id']

    def to_internal_value(self, data):
        if hasattr(data, 'copy') and isinstance(data.get('postcode'), str):  # before the length check
            data = data.copy()
            data['postcode'] = normalise_postcode(data['postcode'])
        return super().to_internal_value(data)

    def validate_postcode(self, value):
        value = normalise_postcode(value)
        problem = postcode_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value


class TradeCustomerContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = TradeCustomerContact
        fields = ['id', 'name', 'role', 'email', 'phone', 'is_primary']
        read_only_fields = ['id']


class TradeCustomerSerializer(TenantModelSerializer):
    """
    A trade customer (G38) with its delivery addresses and contacts. Sending ``addresses`` or ``contacts`` replaces
    them all; leaving one out keeps it. At most one default address and one primary contact (a single one becomes
    it).
    """

    delivery_days = serializers.ListField(
        child=serializers.ChoiceField(choices=WEEKDAYS), required=False,
        help_text='Days it takes deliveries: mon, tue, ... sun, each once.',
    )
    addresses = TradeCustomerAddressSerializer(many=True, required=False)
    contacts = TradeCustomerContactSerializer(many=True, required=False)

    class Meta:
        model = TradeCustomer
        fields = [
            'id', 'account_code', 'legal_name', 'trading_name', 'vat_number', 'status', 'delivery_days', 'note',
            'addresses', 'contacts', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def to_internal_value(self, data):
        # Normalise before the unique (company, account_code) check sees it.
        if hasattr(data, 'copy') and hasattr(data, 'get'):  # a dict, or a QueryDict from a form post
            data = data.copy()
            if isinstance(data.get('account_code'), str):
                data['account_code'] = normalise_account_code(data['account_code'])
            if isinstance(data.get('vat_number'), str):  # before the length check: people type spaces
                data['vat_number'] = normalise_vat_number(data['vat_number'])
        return super().to_internal_value(data)

    def validate_account_code(self, value):
        problem = account_code_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value

    def validate_vat_number(self, value):
        value = normalise_vat_number(value)
        problem = vat_number_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value

    def validate_delivery_days(self, value):
        problem = delivery_days_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return in_week_order(value)

    def validate_addresses(self, value):
        if sum(bool(a.get('is_default')) for a in value) > 1:
            raise serializers.ValidationError('Only one default address.')
        if len(value) == 1:
            value[0]['is_default'] = True
        return value

    def validate_contacts(self, value):
        if sum(bool(c.get('is_primary')) for c in value) > 1:
            raise serializers.ValidationError('Only one primary contact.')
        if len(value) == 1:
            value[0]['is_primary'] = True
        return value

    @staticmethod
    def _replace(customer, addresses, contacts):
        if addresses is not None:
            customer.addresses.all().delete()
            TradeCustomerAddress.objects.bulk_create(TradeCustomerAddress(customer=customer, **a) for a in addresses)
        if contacts is not None:
            customer.contacts.all().delete()
            TradeCustomerContact.objects.bulk_create(TradeCustomerContact(customer=customer, **c) for c in contacts)

    def create(self, validated_data):
        addresses = validated_data.pop('addresses', [])
        contacts = validated_data.pop('contacts', [])
        with transaction.atomic():
            customer = super().create(validated_data)
            self._replace(customer, addresses, contacts)
        return customer

    def update(self, instance, validated_data):
        addresses = validated_data.pop('addresses', None)
        contacts = validated_data.pop('contacts', None)
        with transaction.atomic():
            customer = super().update(instance, validated_data)
            self._replace(customer, addresses, contacts)
        return customer
