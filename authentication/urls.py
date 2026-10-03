from django.urls import path
from .views import AcceptInvitationView, LoginView, PasswordResetConfirmView

urlpatterns = [
    # Exchange credentials for a Bearer token
    path('login/', LoginView.as_view(), name='login'),

    # Confirm a password reset request sent via email
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),

    # Accept a user invitation
    path('invitations/accept/', AcceptInvitationView.as_view(), name='accept-invitation'),
]
