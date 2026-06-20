from django.shortcuts import render
from django.views.generic.list import ListView
from .models import AlternativeSupplier

class AlternativeSupplierListView(ListView):
    """
    A read-only view for displaying a list of all AlternativeSuppliers.

    This view inherits from Django's generic ListView and is designed to handle GET requests,
    rendering a template with a queryset of all AlternativeSupplier objects.
    
    Template:
    - 'logistics/alternative_supplier_list.html'

    Context:
    - 'alternative_suppliers': A list of AlternativeSupplier objects
    """
    model = AlternativeSupplier
    template_name = 'logistics/alternative_supplier_list.html'
    context_object_name = 'alternative_suppliers'