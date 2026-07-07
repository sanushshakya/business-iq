# authentication/tests.py

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from common.exceptions import CustomException
from authentication.models import ShopifyConnection
from authentication.serializers import VerificationTokenSerializer
from authentication.views import VerifyEmailTokenView

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def verification_token_serializer_data():
    return {
        'token': 'test-token',
    }

@pytest.mark.django_db
def test_verify_email_token_success(api_client, verification_token_serializer_data):
    """
    Test the verify-email token endpoint with a valid token.

    Asserts that the response status code is 200 and the message is 'Token verified successfully'.
    """
    url = reverse('verify-email-token')
    serializer = VerificationTokenSerializer(data=verification_token_serializer_data)
    serializer.is_valid(raise_exception=True)

    response = api_client.post(url, data={'token': serializer.data['token']})

    assert response.status_code == 200
    assert response.json() == {'message': 'Token verified successfully'}

@pytest.mark.django_db
def test_verify_email_token_invalid_token(api_client):
    """
    Test the verify-email token endpoint with an invalid token.

    Asserts that the response status code is 400 and the message is 'Invalid or expired token'.
    """
    url = reverse('verify-email-token')
    serializer = VerificationTokenSerializer(data={'token': 'invalid-token'})
    serializer.is_valid(raise_exception=True)

    response = api_client.post(url, data={'token': serializer.data['token']})

    assert response.status_code == 400
    assert response.json() == {'error': 'Invalid or expired token'}

@pytest.mark.django_db
def test_verify_email_token_missing_token(api_client):
    """
    Test the verify-email token endpoint without a token.

    Asserts that the response status code is 400 and the message is 'Token is required'.
    """
    url = reverse('verify-email-token')

    response = api_client.post(url, data={})

    assert response.status_code == 400
    assert response.json() == {'error': 'Token is required'}

@pytest.mark.django_db
def test_verify_email_token_with_exception(api_client, monkeypatch):
    """
    Test the verify-email token endpoint with an exception raised during validation.

    Asserts that the response status code is 500 and the message contains 'Internal Server Error'.
    """
    url = reverse('verify-email-token')
    serializer = VerificationTokenSerializer(data={'token': 'test-token'})
    serializer.is_valid(raise_exception=True)

    def mock_validate_token(value):
        raise CustomException('Internal Server Error')

    monkeypatch.setattr(VerificationTokenSerializer, 'validate_token', mock_validate_token)

    response = api_client.post(url, data={'token': serializer.data['token']})

    assert response.status_code == 500
    assert 'Internal Server Error' in response.json()['error']