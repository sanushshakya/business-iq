"""
Pydantic schemas for authentication-related requests and responses.
"""

from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
    """
    Schema for login requests.

    Fields:
    - email: EmailStr representing the user's email address.
    - password: str representing the user's password.
    """
    email: EmailStr
    password: str = Field(min_length=8)

class RegisterRequest(BaseModel):
    """
    Schema for registration requests.

    Fields:
    - email: EmailStr representing the user's email address.
    - password: str representing the user's password.
    - first_name: str representing the user's first name.
    - last_name: str representing the user's last name.
    """
    email: EmailStr
    password: str = Field(min_length=8)
    first_name: str
    last_name: str

class TokenResponse(BaseModel):
    """
    Schema for token responses.

    Fields:
    - access_token: str representing the JWT access token.
    - refresh_token: str representing the JWT refresh token.
    """
    access_token: str
    refresh_token: str