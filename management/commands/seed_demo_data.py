from django.urls import path
from .views import PasswordResetConfirmView

urlpatterns = [
    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]
```

This file, `authentication/urls.py`, defines the URL patterns for handling password reset confirmations within a Django application. It includes a single URL pattern that maps to the `PasswordResetConfirmView` view, which handles the process of confirming a user's new password after they have requested a password reset via email.