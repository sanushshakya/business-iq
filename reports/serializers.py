from datetime import timedelta

from rest_framework import serializers

from vat.services import uk_date

EARLIEST = 2000


def _money(**kwargs):
    return serializers.DecimalField(max_digits=14, decimal_places=2, **kwargs)


def _kg():
    return serializers.DecimalField(max_digits=14, decimal_places=3)


class DashboardQuerySerializer(serializers.Serializer):
    week = serializers.DateField(required=False, input_formats=['%Y-%m-%d'], help_text='Any day in the week.')

    def validate_week(self, day):
        if day.year < EARLIEST or day > uk_date() + timedelta(days=366):  # (no replace(): 29 February)
            raise serializers.ValidationError('Pick a week from 2000 to a year from now.')
        return day


class _Place(serializers.Serializer):
    branch = serializers.IntegerField(allow_null=True)
    branch_name = serializers.CharField()
    location = serializers.IntegerField(allow_null=True)
    location_code = serializers.CharField(allow_null=True)
    location_name = serializers.CharField(allow_null=True)


class SalesRowSerializer(_Place):
    channel = serializers.ChoiceField(choices=['till', 'trade', 'online'])
    net = _money(help_text='Ex VAT.')
    costed_net = _money(help_text='Sales whose stock had a known cost.')
    cost = _money()
    margin = _money()
    margin_percent = _money(allow_null=True, help_text='Over costed sales; null when none were costed.')
    uncosted_net = _money()
    units = serializers.IntegerField()
    kg = _kg()


class WasteRowSerializer(_Place):
    count = serializers.IntegerField()
    units = serializers.IntegerField()
    kg = _kg()
    cost = _money()
    uncosted = serializers.IntegerField(help_text='Write-offs with no known cost.')


class StockRowSerializer(_Place):
    value = _money(help_text='Costed stock at the end of the period.')
    uncosted_units = serializers.IntegerField()
    uncosted_kg = _kg()


class TotalsSerializer(serializers.Serializer):
    net = _money()
    costed_net = _money()
    cost = _money()
    uncosted_net = _money()
    margin = _money()
    margin_percent = _money(allow_null=True)
    waste_cost = _money()
    stock_value = _money()


class PeriodSerializer(serializers.Serializer):
    start = serializers.DateField()
    end = serializers.DateField()
    sales = SalesRowSerializer(many=True)
    waste = WasteRowSerializer(many=True)
    stock = StockRowSerializer(many=True)
    totals = TotalsSerializer()


class DashboardSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    end = serializers.DateField()
    previous_start = serializers.DateField()
    previous_end = serializers.DateField()
    days = serializers.IntegerField()
    current = PeriodSerializer()
    previous = PeriodSerializer()
