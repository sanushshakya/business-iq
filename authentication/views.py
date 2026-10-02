"""
authentication/views.py

This file contains the views for handling password reset confirmations within a Django application.
"""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from authentication.models import UserInvitation
from authentication.serializers import PasswordResetConfirmSerializer

User = get_user_model()

class PasswordResetConfirmView(APIView):
    """
    View to handle the password reset confirmation request.

    This view expects a POST request with 'token', 'new_password1', and 'new_password2' as parameters.
    It validates the token, sets the new password, and returns a JSON response indicating success or failure.
    """

    def post(self, request, *args, **kwargs):
        """
        Handle the POST request for password reset confirmation.

        Parameters:
        - request: The HTTP request object containing the data to be processed.

        Returns:
        - A JSON response indicating whether the password reset was successful or not.
        """
        try:
            serializer = PasswordResetConfirmSerializer(data=request.data)
            if serializer.is_valid():
                user = self.get_user(serializer.validated_data['uidb64'])
                if user is not None:
                    new_password = serializer.validated_data['new_password1']
                    user.set_password(new_password)
                    user.save()
                    return Response({'detail': _('Password reset successful')}, status=status.HTTP_200_OK)
                else:
                    return Response({'detail': _('Invalid token')}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # Log the exception for debugging purposes
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("An error occurred during password reset: %s", e)

            return Response({'detail': _('Internal server error')}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def get_user(self, uidb64):
        """
        Retrieve the user from the database using the provided UID.

        Parameters:
        - uidb64: The base-64 encoded primary key of the user.

        Returns:
        - The user object if found, otherwise None.
        """
        try:
            # Convert UID to integer
            uid = default_token_generator.make_hashed_password(uidb64)
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None

        return user


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
        user.groups.add(Group.objects.get_or_create(name=invitation.role)[0])
        
        # Mark the invitation as accepted
        invitation.accepted_at = timezone.now()
        invitation.save()

        # Optionally, send a notification or perform other actions

        return Response({'message': 'Invitation accepted successfully'}, status=status.HTTP_201_CREATED)
