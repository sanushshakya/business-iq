# routes/auth.py

from django.urls import path
from .views import RegisterView, LoginView, ForgotPasswordView

urlpatterns = [
    # Route for user registration
    path('register/', RegisterView.as_view(), name='register'),
    
    # Route for user login
    path('login/', LoginView.as_view(), name='login'),
    
    # Route for password reset request
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
]