# config/authentication/views.py

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import uuid
from datetime import timedelta
from .models import UserInvitation
from .serializers import UserInvitationSerializer
from common.utils import send_invitation_email

class AcceptInvitationView(APIView):
    """
    View for accepting user invitations using a valid token.
    
    This view handles the logic for accepting a user invitation by validating the token,
    creating a new user, and associating them with the company specified in the invitation.
    """

    def post(self, request):
        """
        Handle POST requests to accept an invitation.

        :param request: The incoming HTTP request
        :return: A JSON response indicating success or failure
        """
        token = request.data.get('token')
        
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            invitation = UserInvitation.objects.get(token=uuid.UUID(token), expires_at__gt=timezone.now())
        except UserInvitation.DoesNotExist:
            return Response({'error': 'Invalid or expired token'}, status=status.HTTP_400_BAD_REQUEST)

        # Create a new user and associate them with the company
        username = request.data.get('username')
        password = request.data.get('password')

        if not (username and password):
            return Response({'error': 'Username and password are required'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.create_user(username=username, email=invitation.invited_email, password=password)
        
        # Set the user's role
        user.groups.add(get_group_by_name(invitation.role))
        
        # Mark the invitation as accepted
        invitation.accepted_at = timezone.now()
        invitation.save()

        # Optionally, send a notification or perform other actions

        return Response({'message': 'Invitation accepted successfully'}, status=status.HTTP_201_CREATED)