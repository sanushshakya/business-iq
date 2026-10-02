# common/views.py

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DemandAlert, StockAlert
from .serializers import DemandAlertSerializer, StockAlertSerializer, VerificationTokenSerializer
from .services.verification_token_service import VerificationTokenService


class StockAlertListAPIView(generics.ListAPIView):
    """Undismissed stock alerts, newest first."""

    serializer_class = StockAlertSerializer

    def get_queryset(self):
        return StockAlert.objects.filter(is_dismissed=False).order_by('-created_at')


class DemandAlertCreateAPIView(generics.CreateAPIView):
    queryset = DemandAlert.objects.all()
    serializer_class = DemandAlertSerializer


class DemandAlertDismissAPIView(APIView):
    """Mark a demand alert as handled."""

    @extend_schema(request=None, responses=DemandAlertSerializer)
    def post(self, request, pk):
        try:
            alert = DemandAlert.objects.get(pk=pk)
        except DemandAlert.DoesNotExist:
            return Response({'error': 'Demand alert not found'}, status=status.HTTP_404_NOT_FOUND)
        alert.is_handled = True
        alert.save(update_fields=['is_handled'])
        return Response(DemandAlertSerializer(alert).data)


class VerifyEmailTokenView(APIView):
    """Check an email verification token produced by VerificationTokenService."""

    permission_classes = [AllowAny]

    @extend_schema(request=VerificationTokenSerializer, responses=inline_serializer('VerifyEmailTokenResponse', {'message': serializers.CharField(), 'user_id': serializers.IntegerField()}))
    def post(self, request):
        serializer = VerificationTokenSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        user_id = VerificationTokenService.verify_token(serializer.validated_data['token'])
        if user_id is None:
            return Response({'error': 'Invalid or expired token'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'message': 'Token verified successfully', 'user_id': user_id})
