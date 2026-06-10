"""
config/authentication/tests.py

This file contains tests for ensuring that expired tokens are rejected when accepting user invitations.
"""

import uuid
from datetime import timedelta
from django.test import TestCase, RequestFactory
from rest_framework.test import APIClient
from rest_framework.status import HTTP_400_BAD_REQUEST, HTTP_200_OK
from .models import UserInvitation

class ExpiredTokenTests(TestCase):
    """
    Tests for handling expired tokens when accepting user invitations.
    """

    def setUp(self):
        """
        Set up test environment by creating a company and an invitation with an expired token.
        """
        self.client = APIClient()
        self.factory = RequestFactory()
        self.company = Company.objects.create(name="Test Company")
        self.expired_token = uuid.uuid4()
        UserInvitation.objects.create(
            company=self.company,
            invited_email="test@example.com",
            role="Owner",
            token=self.expired_token,
            expires_at=timezone.now() - timedelta(hours=1)
        )

    def test_accept_invitation_with_expired_token(self):
        """
        Test that accepting an invitation with an expired token returns a 400 Bad Request.
        """
        data = {
            'token': self.expired_token
        }
        response = self.client.post('/api/accept-invitation/', data)
        self.assertEqual(response.status_code, HTTP_400_BAD_REQUEST)
        self.assertIn('Token is expired', str(response.data))

    def test_accept_invitation_with_valid_token(self):
        """
        Test that accepting an invitation with a valid token returns a 200 OK.
        """
        # Create a new invitation with a non-expired token
        valid_token = uuid.uuid4()
        UserInvitation.objects.create(
            company=self.company,
            invited_email="test@example.com",
            role="Owner",
            token=valid_token,
            expires_at=timezone.now() + timedelta(hours=1)
        )
        
        data = {
            'token': valid_token
        }
        response = self.client.post('/api/accept-invitation/', data)
        self.assertEqual(response.status_code, HTTP_200_OK)
        self.assertIn('Invitation accepted successfully', str(response.data))