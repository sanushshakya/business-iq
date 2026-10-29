from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .two_factor_views import (
    StaffTwoFactorViewSet,
    TwoFactorDisableView,
    TwoFactorEnrolConfirmView,
    TwoFactorEnrolView,
    TwoFactorStatusView,
    TwoFactorVerifyView,
)
from .views import (
    AcceptInvitationView,
    InvitationViewSet,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RefreshView,
    CodeLoginView,
)

router = DefaultRouter()
router.register(r'invitations', InvitationViewSet, basename='invitation')
router.register(r'2fa/users', StaffTwoFactorViewSet, basename='two-factor-user')

urlpatterns = [
    # Exchange credentials for a Bearer token
    path('login/', LoginView.as_view(), name='login'),

    # Operators sign in with an 8-digit code; /me/ says who is signed in
    path('code-login/', CodeLoginView.as_view(), name='code-login'),
    path('me/', MeView.as_view(), name='me'),

    # Two-factor sign-in: the second step after /login/, set-up, status and switching it off
    path('2fa/', TwoFactorStatusView.as_view(), name='two-factor-status'),
    path('2fa/verify/', TwoFactorVerifyView.as_view(), name='two-factor-verify'),
    path('2fa/enrol/', TwoFactorEnrolView.as_view(), name='two-factor-enrol'),
    path('2fa/enrol/confirm/', TwoFactorEnrolConfirmView.as_view(), name='two-factor-enrol-confirm'),
    path('2fa/disable/', TwoFactorDisableView.as_view(), name='two-factor-disable'),

    # Trade a refresh token for a new token pair / end a session
    path('token/refresh/', RefreshView.as_view(), name='token-refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),

    # Ask for a password reset email / confirm it with the emailed link
    path('password_reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),

    # Accept a user invitation
    path('invitations/accept/', AcceptInvitationView.as_view(), name='accept-invitation'),

    # Staff: invite users and list/revoke invitations (the accept route above must stay first)
    path('', include(router.urls)),
]
