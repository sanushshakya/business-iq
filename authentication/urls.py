from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import (
    AcceptInvitationView,
    InvitationViewSet,
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RefreshView,
)

router = DefaultRouter()
router.register(r'invitations', InvitationViewSet, basename='invitation')

urlpatterns = [
    # Exchange credentials for a Bearer token
    path('login/', LoginView.as_view(), name='login'),

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
