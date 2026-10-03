from django.urls import path
from .views import AcceptInvitationView, LoginView, LogoutView, PasswordResetConfirmView, RefreshView

urlpatterns = [
    # Exchange credentials for a Bearer token
    path('login/', LoginView.as_view(), name='login'),

    # Trade a refresh token for a new token pair / end a session
    path('token/refresh/', RefreshView.as_view(), name='token-refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),

    # Confirm a password reset request sent via email
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),

    # Accept a user invitation
    path('invitations/accept/', AcceptInvitationView.as_view(), name='accept-invitation'),
]
