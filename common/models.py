from django.urls import path
from .views import PasswordResetConfirmView

urlpatterns = [
    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]
```

This file, `authentication/urls.py`, defines the URL patterns for handling password reset confirmations within a Django application. It includes:

- A single URL pattern that maps the `/password_reset/confirm/` endpoint to the `PasswordResetConfirmView`.
- The view is used to handle the confirmation of password reset requests.

The file follows the project's naming convention and imports style, ensuring consistency with the rest of the codebase.