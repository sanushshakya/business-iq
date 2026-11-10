from datetime import date

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import Account, Journal, JournalLine, Period, VatReturn


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
        fields = ['id', 'code', 'name', 'type', 'system_key', 'is_active', 'vat_purchases', 'created_at']
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
                'to': StrictDateField(help_text='Last UK day: yesterday at the latest; at most 93 days.')}


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


class DateRangeSerializer(serializers.Serializer):
    def get_fields(self):  # ``from`` is a Python keyword
        return {'from': StrictDateField(help_text='First UK day (YYYY-MM-DD).'),
                'to': StrictDateField(help_text='Last UK day (YYYY-MM-DD).')}


class ReportRowSerializer(serializers.Serializer):
    account = serializers.IntegerField(allow_null=True)
    code = serializers.CharField(allow_null=True)
    name = serializers.CharField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)


class ProfitAndLossSerializer(serializers.Serializer):
    start = serializers.DateField()
    end = serializers.DateField()
    income = ReportRowSerializer(many=True)
    expenses = ReportRowSerializer(many=True)
    total_income = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_expenses = serializers.DecimalField(max_digits=16, decimal_places=2)
    net_profit = serializers.DecimalField(max_digits=16, decimal_places=2)


class BalanceSheetSerializer(serializers.Serializer):
    on = serializers.DateField()
    assets = ReportRowSerializer(many=True)
    liabilities = ReportRowSerializer(many=True)
    equity = ReportRowSerializer(many=True, help_text='With "Profit to date" (no year-end close yet).')
    total_assets = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_liabilities = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_equity = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_liabilities_and_equity = serializers.DecimalField(max_digits=16, decimal_places=2)


class VatReturnSerializer(serializers.ModelSerializer):
    class Meta:
        model = VatReturn
        fields = ['id', 'period_start', 'period_end', 'box1', 'box2', 'box3', 'box4', 'box5', 'net_direction', 'box6',
                  'box7', 'box8', 'box9', 'filed_by', 'filed_at']
        read_only_fields = fields


class VatBoxesSerializer(serializers.Serializer):
    start = serializers.DateField()
    end = serializers.DateField()
    box1 = serializers.DecimalField(max_digits=16, decimal_places=2, help_text='VAT due on sales.')
    box2 = serializers.DecimalField(max_digits=16, decimal_places=2, help_text='VAT due on EU acquisitions (0).')
    box3 = serializers.DecimalField(max_digits=16, decimal_places=2, help_text='Boxes 1 + 2.')
    box4 = serializers.DecimalField(max_digits=16, decimal_places=2, help_text='VAT reclaimed on purchases.')
    box5 = serializers.DecimalField(max_digits=16, decimal_places=2, help_text='Net VAT, see `net_direction`.')
    net_direction = serializers.ChoiceField(choices=VatReturn.DIRECTIONS)
    box6 = serializers.DecimalField(max_digits=16, decimal_places=0, help_text='Sales ex VAT, whole pounds.')
    box7 = serializers.DecimalField(max_digits=16, decimal_places=0, help_text='Purchases ex VAT, whole pounds.')
    box8 = serializers.DecimalField(max_digits=16, decimal_places=0)
    box9 = serializers.DecimalField(max_digits=16, decimal_places=0)
    filed = VatReturnSerializer(allow_null=True, help_text='The return filed for exactly this period, if any.')
