from datetime import date

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import Account, Journal, JournalLine, Period


class StrictDateField(serializers.DateField):
    """YYYY-MM-DD only (Django also reads 2026-6-1, which is too easy to mistype for a posting date)."""

    def to_internal_value(self, value):
        if not isinstance(value, str) or len(value) != 10:
            self.fail('invalid', format='YYYY-MM-DD')
        return super().to_internal_value(value)


class MonthField(serializers.CharField):
    """``YYYY-MM`` → the month's first day."""

    def to_internal_value(self, value):
        text = super().to_internal_value(value)
        try:
            if len(text) != 7:
                raise ValueError(text)
            return date.fromisoformat(f'{text}-01')
        except ValueError:
            raise serializers.ValidationError('A month as YYYY-MM.') from None


class AccountSerializer(TenantModelSerializer):
    class Meta:
        model = Account
        fields = ['id', 'code', 'name', 'type', 'system_key', 'is_active', 'created_at']
        read_only_fields = ['id', 'system_key', 'created_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        account = self.instance
        if account is not None:
            if account.lines.exists():
                for field in ('code', 'type'):
                    if field in attrs and attrs[field] != getattr(account, field):
                        raise serializers.ValidationError({field: 'Fixed once something is posted to it.'})
            if account.system_key and attrs.get('is_active') is False:
                raise serializers.ValidationError({'is_active': 'Automatic postings use this account; it stays '
                                                                'active.'})
        return attrs


class JournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)

    class Meta:
        model = JournalLine
        fields = ['id', 'account', 'account_code', 'account_name', 'debit', 'credit', 'description']
        read_only_fields = fields


class JournalSerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField(help_text='The debits (= the credits).')
    reversed_by = serializers.SerializerMethodField(help_text='The journal that reverses this one, if any.')

    class Meta:
        model = Journal
        fields = ['id', 'number', 'date', 'narrative', 'source', 'source_ref', 'reverses', 'reversed_by', 'reason',
                  'created_by', 'created_at', 'total', 'lines']
        read_only_fields = fields

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=2))
    def get_total(self, journal):
        return f'{sum(line.debit for line in journal.lines.all()):.2f}'

    def get_reversed_by(self, journal) -> int | None:
        reversal = getattr(journal, 'reversed_by', None)
        return reversal.pk if reversal is not None else None


class JournalLineInputSerializer(serializers.Serializer):
    account = serializers.IntegerField()
    debit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    credit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    description = serializers.CharField(max_length=120, required=False, allow_blank=True)


class JournalCreateSerializer(serializers.Serializer):
    date = StrictDateField(help_text='YYYY-MM-DD; its month must be open.')
    narrative = serializers.CharField(max_length=255)
    lines = JournalLineInputSerializer(many=True, help_text='At least two; each a debit or a credit; they balance.')


class ReverseSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
    date = StrictDateField(required=False, help_text='Default today (UK).')


class PeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = Period
        fields = ['id', 'start', 'locked', 'locked_at', 'locked_by']
        read_only_fields = fields


class LockSerializer(serializers.Serializer):
    month = MonthField(help_text='YYYY-MM: it and every month before it are locked.')


class ReopenSerializer(serializers.Serializer):
    month = MonthField(help_text='YYYY-MM: it and every locked month after it are reopened.')
    reason = serializers.CharField(max_length=255)


class LockStateSerializer(serializers.Serializer):
    locked_through = serializers.DateField(allow_null=True, help_text='The last locked month (its 1st), or null.')
    periods = PeriodSerializer(many=True)


class PostingRunRequestSerializer(serializers.Serializer):
    # ``from`` is a Python keyword, so the two fields are named in ``get_fields``.
    def get_fields(self):
        return {'from': StrictDateField(help_text='First UK day.'),
                'to': StrictDateField(help_text='Last UK day: yesterday at the latest; at most 366 days.')}


class PostingConflictSerializer(serializers.Serializer):
    source = serializers.CharField()
    ref = serializers.CharField()
    detail = serializers.CharField()


class PostingRunSerializer(serializers.Serializer):
    posted = serializers.IntegerField(help_text='New journals.')
    already = serializers.IntegerField(help_text='Posted before: nothing new.')
    moved = serializers.IntegerField(help_text='Dated in a locked month: posted on the first open day.')
    skipped = serializers.IntegerField(help_text='Write-offs and breakdowns with no cost.')
    conflicts = PostingConflictSerializer(many=True)
    error = serializers.CharField(allow_blank=True)


class TrialBalanceRowSerializer(serializers.Serializer):
    account = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    debit = serializers.DecimalField(max_digits=16, decimal_places=2)
    credit = serializers.DecimalField(max_digits=16, decimal_places=2)


class TrialBalanceSerializer(serializers.Serializer):
    on = serializers.DateField(allow_null=True)
    rows = TrialBalanceRowSerializer(many=True)
    debit = serializers.DecimalField(max_digits=16, decimal_places=2)
    credit = serializers.DecimalField(max_digits=16, decimal_places=2)
