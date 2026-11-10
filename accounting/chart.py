"""The default UK small-business chart of accounts (G52); ``system_key`` is how automatic postings find one."""

from django.db import transaction

ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = 'asset', 'liability', 'equity', 'income', 'expense'

# (code, name, type, system key or None)
DEFAULT_CHART = [
    ('1000', 'Bank current account', ASSET, 'bank'),
    ('1010', 'Cash in tills', ASSET, 'cash'),
    ('1020', 'Card clearing', ASSET, 'card_clearing'),
    ('1100', 'Trade debtors', ASSET, 'debtors'),
    ('1200', 'Stock', ASSET, 'stock'),
    ('2100', 'Trade creditors', LIABILITY, 'creditors'),
    ('2200', 'VAT on sales', LIABILITY, 'vat_output'),
    ('2210', 'VAT on purchases', LIABILITY, 'vat_input'),
    ('2220', 'VAT due to HMRC', LIABILITY, 'vat_hmrc'),
    ('3000', 'Capital', EQUITY, 'capital'),
    ('3100', 'Retained earnings', EQUITY, 'retained_earnings'),
    ('4000', 'Sales - standard rate', INCOME, 'sales_standard'),
    ('4010', 'Sales - reduced rate', INCOME, 'sales_reduced'),
    ('4020', 'Sales - zero rate', INCOME, 'sales_zero'),
    ('4030', 'Sales - exempt', INCOME, 'sales_exempt'),
    ('4900', 'Other income', INCOME, None),
    ('5000', 'Purchases', EXPENSE, 'purchases'),
    ('5100', 'Stock wastage', EXPENSE, 'wastage'),
    ('5110', 'Butchery yield loss', EXPENSE, 'yield_loss'),
    ('5200', 'Carriage inwards', EXPENSE, None),
    ('7000', 'Wages', EXPENSE, None),
    ('7100', 'Rent and rates', EXPENSE, None),
    ('7200', 'Utilities', EXPENSE, None),
    ('7500', 'Bank charges', EXPENSE, None),
    ('7510', 'Card fees', EXPENSE, None),
    ('9999', 'Suspense', ASSET, 'suspense'),
]
SYSTEM_KEYS = {key for *_rest, key in DEFAULT_CHART if key}
# Counted in VAT return box 7 (G54): purchases and overheads that carry VAT or are zero-rated/exempt, not wages.
VAT_PURCHASE_CODES = {'5000', '5200', '7100', '7200', '7510'}


def ensure_default_chart(company, account_model=None):
    """Add the default accounts the company lacks (by code, and by system key); returns how many. Renamed or
    changed ones are left as they are."""
    if account_model is None:
        from .models import Account as account_model
    with transaction.atomic():
        have = set(account_model.objects.filter(company=company).values_list('code', flat=True))
        keys = set(account_model.objects.filter(company=company).exclude(system_key=None)
                   .values_list('system_key', flat=True))
        new = [account_model(company=company, code=code, name=name, type=kind,
                             system_key=key if key not in keys else None, vat_purchases=code in VAT_PURCHASE_CODES)
               for code, name, kind, key in DEFAULT_CHART if code not in have]
        account_model.objects.bulk_create(new)
    return len(new)
