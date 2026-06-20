# logistics/views.py

from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from .models import UserSupplier, AlternativeSupplier, FreightAlert

class UserSupplierCreateView(CreateView):
    """
    View for creating a new UserSupplier instance.

    Handles the creation form and saves the new instance to the database.
    """
    model = UserSupplier
    fields = ['supplier_name', 'contact_info']
    success_url = reverse_lazy('user_supplier_list')

    def form_valid(self, form):
        """
        Validate the form data and save the new UserSupplier instance.

        :param form: Form containing the user supplier data.
        :return: Redirect to list view on successful creation.
        """
        response = super().form_valid(form)
        return response

class UserSupplierUpdateView(UpdateView):
    """
    View for updating an existing UserSupplier instance.

    Handles the update form and updates the instance in the database.
    """
    model = UserSupplier
    fields = ['supplier_name', 'contact_info']
    success_url = reverse_lazy('user_supplier_list')

    def get_object(self):
        """
        Retrieve the UserSupplier object based on the URL parameter.

        :return: UserSupplier instance matching the primary key.
        """
        return get_object_or_404(UserSupplier, pk=self.kwargs['pk'])

class UserSupplierDeleteView(DeleteView):
    """
    View for deleting an existing UserSupplier instance.

    Handles the deletion of a UserSupplier instance from the database.
    """
    model = UserSupplier
    success_url = reverse_lazy('user_supplier_list')

    def get_object(self):
        """
        Retrieve the UserSupplier object based on the URL parameter.

        :return: UserSupplier instance matching the primary key.
        """
        return get_object_or_404(UserSupplier, pk=self.kwargs['pk'])

def user_supplier_list(request):
    """
    View for listing all UserSupplier instances.

    Displays a list of all UserSuppliers in the database.
    """
    usersuppliers = UserSupplier.objects.all()
    context = {'usersuppliers': usersuppliers}
    return render(request, 'logistics/user_supplier_list.html', context)

class AlternativeSupplierReadView(DetailView):
    """
    View for reading a specific AlternativeSupplier instance.

    Displays details of an AlternativeSupplier instance.
    """
    model = AlternativeSupplier
    template_name = 'logistics/alternative_supplier_detail.html'

    def get_object(self):
        """
        Retrieve the AlternativeSupplier object based on the URL parameter.

        :return: AlternativeSupplier instance matching the primary key.
        """
        return get_object_or_404(AlternativeSupplier, pk=self.kwargs['pk'])