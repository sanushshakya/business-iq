# config/authentication/views.py

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from .models import UserInvitation
from .serializers import UserInvitationSerializer

@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdminUser])
def invite_user(request):
    """
    API endpoint for inviting users. Only accessible to admin/manager users.
    
    Parameters:
    request (Request): The HTTP POST request containing the user invitation data.

    Returns:
    Response: A JSON response indicating success or failure of the invitation.
    """
    serializer = UserInvitationSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)