from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth.tokens import default_token_generator
from django.utils.translation import gettext as _
from authentication.serializers import PasswordResetConfirmSerializer

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
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if serializer.is_valid():
            user = self.get_user(serializer.validated_data['token'])
            if user is not None:
                new_password = serializer.validated_data['new_password1']
                user.set_password(new_password)
                user.save()
                return Response({'detail': _('Password reset successful')}, status=status.HTTP_200_OK)
            else:
                return Response({'detail': _('Invalid token')}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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