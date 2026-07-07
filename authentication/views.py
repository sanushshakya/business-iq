from django.http import JsonResponse
from rest_framework.views import APIView
from .serializers import VerificationTokenSerializer

class VerifyEmailTokenView(APIView):
    """
    View to handle the verification of email tokens.

    This view expects a POST request with 'token' as the token parameter.
    It validates the token and returns a JSON response indicating success or failure.
    """

    def post(self, request, *args, **kwargs):
        serializer = VerificationTokenSerializer(data=request.data)
        if not serializer.is_valid():
            return JsonResponse({'error': 'Invalid token'}, status=400)

        token = serializer.validated_data['token']
        # Logic to verify the email token
        # This could involve checking a database or calling an external service
        is_verified = self.verify_token(token)
        
        if is_verified:
            return JsonResponse({'message': 'Email verified successfully'}, status=200)
        else:
            return JsonResponse({'error': 'Invalid or expired token'}, status=401)

    def verify_token(self, token):
        """
        Placeholder method for verifying the email token.

        Args:
            token (str): The verification token to validate.

        Returns:
            bool: True if the token is valid, False otherwise.
        """
        # Actual implementation of token verification logic goes here
        return True  # Placeholder