from django.db import models

class User(models.Model):
    """
    Model representing a user of the application.

    Fields:
    - username: CharField representing the username of the user.
    - email: EmailField representing the email address of the user.
    - password_hash: CharField representing the hashed password of the user.
    - reset_token: CharField representing the token used for resetting the user's password.
    - reset_token_expiry: DateTimeField representing the expiration time of the reset token.

    Methods:
    - set_password(password): Hashes the provided password and saves it to the model.
    """

    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    password_hash = models.CharField(max_length=256)
    reset_token = models.CharField(max_length=100, null=True, blank=True)
    reset_token_expiry = models.DateTimeField(null=True, blank=True)

    def set_password(self, password):
        """
        Hashes the provided password using PBKDF2 and saves it to the model.

        Args:
        - password (str): The plain text password to hash.
        """
        self.password_hash = pbkdf2_hmac('sha256', password.encode(), get_random_string(12).encode(), 100000)

    def check_password(self, password):
        """
        Checks if the provided password matches the hashed password.

        Args:
        - password (str): The plain text password to check.

        Returns:
        - bool: True if the passwords match, False otherwise.
        """
        return pbkdf2_hmac('sha256', password.encode(), get_random_string(12).encode(), 100000) == self.password_hash