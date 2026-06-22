"""
JWT Handler for encoding and decoding JSON Web Tokens with an expiration time.

This module provides utility functions to handle JWT creation and validation,
ensuring secure and timely authentication tokens are used throughout the application.
"""

import jwt
from datetime import datetime, timedelta

# Secret key for JWT encoding/decoding. Replace this with a secure secret in production.
JWT_SECRET_KEY = 'your-very-secret-key'

def encode_jwt(payload, expires_in=3600):
    """
    Encode a payload into a JWT.

    Args:
        payload (dict): The data to be encoded into the JWT.
        expires_in (int, optional): Time in seconds until the token expires. Defaults to 3600 seconds (1 hour).

    Returns:
        str: The encoded JWT.
    """
    # Calculate expiration time
    expire = datetime.utcnow() + timedelta(seconds=expires_in)
    payload['exp'] = expire
    
    # Encode and return the JWT
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm='HS256')
    return token

def decode_jwt(token):
    """
    Decode a JWT and verify its signature.

    Args:
        token (str): The JWT to be decoded.

    Returns:
        dict: The decoded payload if the token is valid.
    
    Raises:
        jwt.ExpiredSignatureError: If the token has expired.
        jwt.InvalidTokenError: If the token is invalid or tampered with.
    """
    # Decode and return the payload
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        raise jwt.ExpiredSignatureError("Token has expired")
    except jwt.InvalidTokenError:
        raise jwt.InvalidTokenError("Invalid token")

# Example usage:
# payload = {'user_id': 123}
# encoded_token = encode_jwt(payload)
# decoded_payload = decode_jwt(encoded_token)