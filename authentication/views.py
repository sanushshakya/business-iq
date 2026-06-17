from django.shortcuts import render, redirect
from django.views.generic.edit import CreateView
from django.contrib.auth.models import User
from .models import Company, Owner
from common.models import ShopifyConnection

class RegisterView(CreateView):
    """
    View for user registration that creates a Company + Owner user in one transaction.
    """
    template_name = 'authentication/register.html'
    success_url = '/login/'

    def form_valid(self, form):
        """
        Handle the logic for creating a new user and associated company/owner.
        """
        user = form.save()
        company = Company.objects.create(user=user)
        Owner.objects.create(company=company, user=user)
        return super().form_valid(form)


class ShopifyOAuthStartView(View):
    """
    View to initiate the OAuth process with Shopify.
    """

    def get(self, request):
        """
        Redirect the user to Shopify's authorization URL.
        """
        shop_domain = request.GET.get('shop', '')
        if not shop_domain:
            return redirect('/login/')

        # Generate a random state parameter
        state = get_random_string(length=32)

        # Store the state and shop domain in session for later verification
        request.session['oauth_state'] = state
        request.session['oauth_shop'] = shop_domain

        # Construct the authorization URL
        redirect_uri = f"{request.build_absolute_uri('/auth/callback/')}"
        auth_url = f"https://{shop_domain}/admin/oauth/authorize?client_id=YOUR_CLIENT_ID&scope=read_products,write_products&state={state}&redirect_uri={redirect_uri}"

        return redirect(auth_url)


class ShopifyOAuthCallbackView(View):
    """
    View to handle the callback from Shopify after authorization.
    """

    def get(self, request):
        """
        Handle the OAuth callback and exchange the code for an access token.
        """
        state = request.GET.get('state')
        code = request.GET.get('code')

        # Verify the state parameter
        if not state or state != request.session.pop('oauth_state', None):
            return render(request, 'authentication/oauth_error.html', {'error': 'Invalid state parameter'})

        shop_domain = request.session.pop('oauth_shop')

        # Exchange the code for an access token
        response = requests.post(
            f"https://{shop_domain}/admin/oauth/access_token",
            data={
                'client_id': 'YOUR_CLIENT_ID',
                'client_secret': 'YOUR_CLIENT_SECRET',
                'code': code,
                'grant_type': 'authorization_code'
            }
        )

        if response.status_code != 200:
            return render(request, 'authentication/oauth_error.html', {'error': 'Failed to exchange code for access token'})

        access_token = response.json().get('access_token')

        # Save the access token in the database
        ShopifyConnection.objects.create(
            company=Company.objects.get(user=request.user),
            shop_domain=shop_domain,
            access_token_encrypted=encrypt(access_token)
        )

        return redirect('/dashboard/')