from django.http import HttpResponse
from django.views.generic import FormView
from django.contrib.auth.models import User
from .models import Company, Owner
from common.services.shopify_service import ShopifyService

class SendVerificationEmailView(FormView):
    """
    View to handle the email sending logic for user verification.
    
    This view expects a POST request with 'email' as one of the parameters.
    It checks if a user with the provided email exists and sends a verification email if found.
    """
    template_name = 'authentication/send_verification_email.html'

    def post(self, request, *args, **kwargs):
        email = request.POST.get('email')
        
        try:
            user = User.objects.get(email=email)
            shopify_service = ShopifyService()
            shopify_service.send_verification_email(user)
            
            return HttpResponse("Verification email sent successfully.", status=200)
        except User.DoesNotExist:
            return HttpResponse("No user found with the provided email.", status=404)

    def get(self, request, *args, **kwargs):
        return HttpResponse("This view only accepts POST requests.", status=405)