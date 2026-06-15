"""
config/pricing/views.py

This file contains Django REST Framework (DRF) ViewSets for the pricing app.
"""

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import PricePlan, SupplierInvoice
from .serializers import PricePlanSerializer, SupplierInvoiceSerializer


class PricePlanViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows price plans to be viewed or edited.
    """

    queryset = PricePlan.objects.all()
    serializer_class = PricePlanSerializer

    def perform_create(self, serializer):
        """
        Custom method to create a new price plan and assign the requesting user as its creator.

        :param serializer: Serializer instance containing validated data
        """
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        """
        Custom method to update an existing price plan and record the last updated user.

        :param serializer: Serializer instance containing validated data
        """
        serializer.save(last_updated_by=self.request.user)


class SupplierInvoiceViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows supplier invoices to be viewed, created, or edited.
    """

    queryset = SupplierInvoice.objects.all()
    serializer_class = SupplierInvoiceSerializer

    def perform_create(self, serializer):
        """
        Custom method to create a new supplier invoice and assign the requesting user as its creator.

        :param serializer: Serializer instance containing validated data
        """
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['post'], url_path='invoices')
    def upload_invoice(self, request):
        """
        API endpoint for uploading a supplier invoice with file support.

        :param request: Request object containing the uploaded file and other necessary data
        :return: Response object indicating success or failure of the upload
        """
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No file provided'}, status=400)

        invoice_data = {
            'supplier_name': request.data.get('supplier_name'),
            'invoice_number': request.data.get('invoice_number'),
            'amount': request.data.get('amount'),
            'file': file,
            'created_by': self.request.user
        }

        serializer = SupplierInvoiceSerializer(data=invoice_data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        else:
            return Response(serializer.errors, status=400)