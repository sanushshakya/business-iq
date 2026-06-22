from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from datetime import datetime, timedelta
from pydantic import ValidationError

# Import necessary settings and models
from config.settings import SECRET_KEY
from authentication.models import User

# Dependency to decode JWT and return current user
security = HTTPBearer()

async def get_current_user(token: HTTPAuthorizationCredentials = Depends(security)):
    """
    Decode JWT token and return the corresponding user.

    Args:
        token (HTTPAuthorizationCredentials): The authorization credentials containing the JWT token.

    Returns:
        User: The decoded user object if successful, otherwise raises an exception.
    
    Raises:
        HTTPException: If the token is invalid or expired.
    """
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=["HS256"])
        exp_time = datetime.fromtimestamp(payload["exp"])
        if exp_time < datetime.utcnow():
            raise credentials_exception
    except (JWTError, ValidationError):
        raise credentials_exception

    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    try:
        user = User.objects.get(username=user_id)
    except User.DoesNotExist:
        raise credentials_exception

    return user