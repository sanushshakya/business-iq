# authentication/admin_login.py

"""The Django admin's sign-in form, with the same two-factor rule as /auth/login/ (admins must use it)."""

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError

from . import two_factor


class TwoFactorAdminAuthenticationForm(AdminAuthenticationForm):
    code = forms.CharField(
        label='Two-factor code', required=False, max_length=30,
        help_text='The 6-digit code from your authenticator app, or a recovery code.',
        widget=forms.TextInput(attrs={'autocomplete': 'one-time-code'}),
    )

    def clean(self):
        cleaned = super().clean()
        user = self.get_user()
        if user is None:
            return cleaned
        if two_factor.is_enabled(user):
            if not two_factor.check_second_factor(user, cleaned.get('code')):
                raise ValidationError('Enter a current two-factor code (or a recovery code).', code='two_factor')
        elif two_factor.is_required(user):
            raise ValidationError(
                'Set up two-factor sign-in first: sign in to the web app, which walks you through it.',
                code='two_factor_setup',
            )
        return cleaned
