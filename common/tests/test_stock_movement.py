from django.urls import path
from .views import PasswordResetConfirmView

# URL configurations for authentication-related views
urlpatterns = [
    # URL pattern for password reset confirmation
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]