from django.urls import path
from .views import invite_user, accept_invitation

urlpatterns = [
    # URL pattern for inviting users
    path('invite/', invite_user, name='invite-user'),
    
    # URL pattern for accepting invitations
    path('accept/<str:token>/', accept_invitation, name='accept-invitation'),
]