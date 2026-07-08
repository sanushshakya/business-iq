from pydantic import BaseModel

class LoginRequest(BaseModel):
    """
    Schema for login request.

    Fields:
    - username: str representing the user's username.
    - password: str representing the user's password.
    """

    username: str
    password: str


class RegisterRequest(BaseModel):
    """
    Schema for registration request.

    Fields:
    - username: str representing the user's username.
    - email: str representing the user's email address.
    - password: str representing the user's password.
    - password_confirm: str representing the confirmation of the user's password.
    """

    username: str
    email: str
    password: str
    password_confirm: str


class TokenResponse(BaseModel):
    """
    Schema for token response.

    Fields:
    - access_token: str representing the JWT access token.
    - refresh_token: str representing the JWT refresh token.
    """

    access_token: str
    refresh_token: str