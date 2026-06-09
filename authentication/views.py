from django.shortcuts import render, redirect
from django.views.generic.edit import CreateView
from django.contrib.auth.models import User
from .models import Company, Owner

class RegisterView(CreateView):
    """
    View for user registration that creates a Company + Owner user in one transaction.
    """
    template_name = 'authentication/register.html'
    success_url = '/login/'

    def form_valid(self, form):
        """
        Handle the logic for creating a new user and associated company/owner record in a single transaction.
        """
        # Extract data from form
        username = form.cleaned_data['username']
        password = form.cleaned_data['password1']
        email = form.cleaned_data['email']
        company_name = form.cleaned_data['company_name']

        # Create the new user
        user = User.objects.create_user(username=username, password=password, email=email)

        # Create the associated company and owner
        company = Company(name=company_name)
        owner = Owner(user=user, company=company)
        company.save()
        owner.save()

        return super().form_valid(form)