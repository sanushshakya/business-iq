# auth/password.py

import bcrypt

class PasswordHasher:
    """
    Utility class to hash and verify passwords using bcrypt.

    Methods:
        - hash_password(password: str) -> bytes: Hashes a plain-text password.
        - check_password(hashed_password: bytes, password: str) -> bool: Verifies if the provided password matches the hashed password.
    """

    @staticmethod
    def hash_password(password: str) -> bytes:
        """
        Hashes a plain-text password using bcrypt.

        Args:
            password (str): The plain-text password to hash.

        Returns:
            bytes: The hashed password.
        """
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt)

    @staticmethod
    def check_password(hashed_password: bytes, password: str) -> bool:
        """
        Verifies if the provided password matches the hashed password.

        Args:
            hashed_password (bytes): The hashed password.
            password (str): The plain-text password to verify.

        Returns:
            bool: True if the passwords match, False otherwise.
        """
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password)