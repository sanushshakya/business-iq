# vat/models.py

from django.db import models

ZERO, REDUCED, STANDARD, EXEMPT = 'zero', 'reduced', 'standard', 'exempt'
VAT_CODES = [
    (ZERO, 'Zero-rated'),
    (REDUCED, 'Reduced rate'),
    (STANDARD, 'Standard rate'),
    (EXEMPT, 'Exempt'),
]


class VatRate(models.Model):
    """
    The UK VAT rate for a code from ``valid_from`` until the next row for that code starts.

    Shared reference data (UK rates are national): readable by everyone signed in, changed only by superusers in
    the admin. Sale lines copy the rate they used, so a change here never alters a past sale.
    """

    code = models.CharField(max_length=10, choices=VAT_CODES)
    rate = models.DecimalField(max_digits=5, decimal_places=2, help_text='Percent, e.g. 20.00.')
    valid_from = models.DateField()

    class Meta:
        ordering = ['code', '-valid_from']
        constraints = [models.UniqueConstraint(fields=['code', 'valid_from'], name='one_vat_rate_per_code_per_day')]

    def __str__(self):
        return f'{self.get_code_display()} {self.rate}% from {self.valid_from}'
