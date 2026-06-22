# auth/password.py

import bcrypt
from passlib.context import CryptContext

# Initialize a password context with preferred hashing algorithm and settings
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """
    Hash a plain-text password using bcrypt.
    
    Args:
        password (str): The plain-text password to be hashed.
        
    Returns:
        str: The hashed password.
    """
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a hashed password.
    
    Args:
        plain_password (str): The plain-text password to be verified.
        hashed_password (str): The previously hashed password.
        
    Returns:
        bool: True if the passwords match, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)