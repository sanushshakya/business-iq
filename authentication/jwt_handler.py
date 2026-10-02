"""
JWT Handler for encoding and decoding JSON Web Tokens with an expiration time.
"""

import jwt
from datetime import datetime, timedelta

class JWTHandler:
    """
    Class responsible for handling JWT encoding and decoding operations.

    Attributes:
    - SECRET_KEY (str): Secret key used for signing the JWTs.
    - ALGORITHM (str): Algorithm used to sign the JWTs.
    - EXPIRATION_TIME (int): Expiration time in seconds for the JWTs.
    """

    SECRET_KEY = 'your_secret_key_here'
    ALGORITHM = 'HS256'
    EXPIRATION_TIME = 3600  # 1 hour

    @classmethod
    def encode_token(cls, user_id):
        """
        Encode a JWT containing the user ID with an expiration time.

        Args:
            user_id (int): The user's ID to be encoded in the token.

        Returns:
            str: Encoded JWT.
        """

        try:
            payload = {
                'user_id': user_id,
                'exp': datetime.utcnow() + timedelta(seconds=cls.EXPIRATION_TIME)
            }
            return jwt.encode(payload, cls.SECRET_KEY, algorithm=cls.ALGORITHM)
        except Exception as e:
            raise ValueError('Failed to encode token') from e

    @classmethod
    def decode_token(cls, token):
        """
        Decode a JWT and validate its expiration time.

        Args:
            token (str): The JWT to be decoded.

        Returns:
            dict: Decoded payload if the token is valid, None otherwise.
        """

        try:
            payload = jwt.decode(token, cls.SECRET_KEY, algorithms=[cls.ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError('Token has expired')
        except jwt.InvalidTokenError:
            raise ValueError('Invalid token')

# Example usage:
# encoded_token = JWTHandler.encode_token(123)
# decoded_payload = JWTHandler.decode_token(encoded_token)