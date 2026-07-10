from django.urls import path
from .views import PasswordResetConfirmView

urlpatterns = [
    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]