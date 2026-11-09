"""
The double-entry core (G52): a company's chart of accounts, its journals (balanced, numbered, never changed or
deleted: a mistake is reversed) and its calendar-month periods, locked by staff. ``accounting/journal.py`` is the
only writer of journals and periods.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import pre_delete
from django.dispatch import receiver

JOURNAL_FROZEN = 'A journal is never changed or deleted; post its reversal.'


class Account(models.Model):
    ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = 'asset', 'liability', 'equity', 'income', 'expense'
    TYPES = [(ASSET, 'Asset'), (LIABILITY, 'Liability'), (EQUITY, 'Equity'), (INCOME, 'Income'),
             (EXPENSE, 'Expense')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='accounts')
    code = models.CharField(max_length=6, validators=[RegexValidator(r'^\d{3,6}$', '3 to 6 digits.')])
    name = models.CharField(max_length=80)
    type = models.CharField(max_length=10, choices=TYPES)
    system_key = models.CharField(max_length=30, null=True, blank=True, editable=False,
                                  help_text='What automatic postings (G53) find it by; kept active.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code', 'id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'code'], name='unique_account_code'),
            models.UniqueConstraint(fields=['company', 'system_key'], name='unique_account_system_key',
                                    condition=Q(system_key__isnull=False)),
        ]

    def __str__(self):
        return f'{self.code} {self.name}'


class FrozenQuerySet(models.QuerySet):
    """Only new rows: no bulk path changes or deletes a journal or its lines."""

    def update(self, **kwargs):
        raise ValidationError(JOURNAL_FROZEN)

    def delete(self):
        raise ValidationError(JOURNAL_FROZEN)

    def bulk_update(self, objs, fields, *args, **kwargs):
        raise ValidationError(JOURNAL_FROZEN)

    def bulk_create(self, objs, *args, update_conflicts=False, **kwargs):
        if update_conflicts:
            raise ValidationError(JOURNAL_FROZEN)
        return super().bulk_create(objs, *args, **kwargs)


class _Frozen(models.Model):
    objects = FrozenQuerySet.as_manager()

    class Meta:
        abstract = True
        base_manager_name = 'objects'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(JOURNAL_FROZEN)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(JOURNAL_FROZEN)


class Journal(_Frozen):
    MANUAL = 'manual'

    company = models.ForeignKey('tenants.Company', on_delete=models.PROTECT, related_name='journals')
    number = models.PositiveIntegerField(help_text='Per company: 1, 2, 3...')
    date = models.DateField(help_text='The UK day it belongs to; its month must be open.')
    narrative = models.CharField(max_length=255)
    source = models.CharField(max_length=30, default=MANUAL, help_text='manual, or what posted it (G53).')
    source_ref = models.CharField(max_length=60, null=True, blank=True,
                                  help_text='The source document: posted once per company and source.')
    reverses = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='reversed_by')
    reason = models.CharField(max_length=255, blank=True, help_text="A reversal's reason.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(_Frozen.Meta):
        ordering = ['date', 'number']
        indexes = [models.Index(fields=['company', 'date'], name='journal_company_date')]
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], name='unique_journal_number'),
            models.UniqueConstraint(fields=['company', 'source', 'source_ref'], name='unique_journal_source_ref',
                                    condition=Q(source_ref__isnull=False)),
        ]

    def __str__(self):
        return f'Journal {self.number}'


class JournalLine(_Frozen):
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='lines')
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    description = models.CharField(max_length=120, blank=True)

    class Meta(_Frozen.Meta):
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                condition=(Q(debit__gt=0, credit=0) | Q(debit=0, credit__gt=0)), name='journal_line_one_side'),
        ]

    def __str__(self):
        return f'{self.account} {self.debit or -self.credit}'


class Period(models.Model):
    """A calendar month (``start`` is its 1st). Locked: nothing is posted into it or any earlier month."""

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='accounting_periods')
    start = models.DateField()
    locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='+')

    class Meta:
        ordering = ['start']
        constraints = [
            models.UniqueConstraint(fields=['company', 'start'], name='unique_period_start'),
            models.CheckConstraint(condition=Q(start__day=1), name='period_starts_on_the_first'),
        ]

    def __str__(self):
        return f'{self.start:%B %Y}'


class PeriodEvent(models.Model):
    """The log of locking and reopening months."""

    LOCK, REOPEN = 'lock', 'reopen'
    ACTIONS = [(LOCK, 'Locked'), (REOPEN, 'Reopened')]

    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name='events')
    action = models.CharField(max_length=6, choices=ACTIONS)
    reason = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                             related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at', '-id']


@receiver(pre_delete, sender=Journal)
@receiver(pre_delete, sender=JournalLine)
def _journals_are_kept(sender, instance, **kwargs):
    raise ValidationError(JOURNAL_FROZEN)
