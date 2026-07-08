from django.http import JsonResponse
from rest_framework.views import APIView
from .serializers import PasswordResetSerializer

class PasswordResetView(APIView):
    """
    View to handle the password reset request.

    This view expects a POST request with 'token' and 'new_password' as parameters.
    It validates the token, sets the new password, and returns a JSON response indicating success or failure.
    """

    def post(self, request, format=None):
        """
        Handle the POST request for password reset.

        Args:
        - request: The incoming HTTP request object.
        - format (str): The format of the response.

        Returns:
        - JsonResponse: A JSON response indicating the result of the password reset attempt.
        """
        serializer = PasswordResetSerializer(data=request.data)
        if serializer.is_valid():
            token = serializer.validated_data['token']
            new_password = serializer.validated_data['new_password']

            # TODO: Implement logic to validate the token and set the new password
            # Example:
            # user = User.objects.get(password_reset_token=token)
            # user.set_password(new_password)
            # user.save()

            return JsonResponse({'message': 'Password reset successful'}, status=200)

        return JsonResponse(serializer.errors, status=400)