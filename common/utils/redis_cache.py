# common/utils/redis_cache.py
"""
Module for caching API responses using Redis.

This module provides a decorator to cache API responses for one day, reducing the number of requests
to external services like AlAdhan.com's Hijri calendar API.
"""

import redis
from functools import wraps
from django.conf import settings

# Initialize Redis connection
redis_client = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)

def cache_api_response(timeout=86400):
    """
    Decorator to cache API responses for the specified timeout (default is 24 hours).

    Args:
    - timeout (int): The number of seconds to cache the response. Default is 86400 (24 hours).
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create a unique key based on function name and arguments
            cache_key = f"api:{func.__name__}:{hash(tuple(args) + tuple(kwargs.items()))}"
            
            # Try to get cached response from Redis
            if redis_client.exists(cache_key):
                return eval(redis_client.get(cache_key))
            
            # If not cached, call the function and store the result in Redis
            result = func(*args, **kwargs)
            redis_client.setex(cache_key, timeout, repr(result))
            
            return result
        
        return wrapper
    
    return decorator

# Example usage:
# @cache_api_response()
# def fetch_hijri_calendar():
#     """
#     Function to fetch Hijri calendar data from AlAdhan.com API.
#     """
#     import requests
#     response = requests.get("https://api.aladhan.com/v1/gregorianToHijri")
#     return response.json()
```

This file defines a `cache_api_response` decorator that can be used to cache the results of any function for a specified number of seconds (default is 24 hours). The decorator generates a unique cache key based on the function name and its arguments, checks if the result is already cached in Redis, and if not, calls the function, caches the result, and then returns it.