# tenants/views.py

from django.http import Http404
from rest_framework import viewsets
from rest_framework.generics import RetrieveUpdateAPIView

from common.tenancy import IsCompanyStaffOrReadOnly, TenantScopedMixin

from .models import Branch, Till
from .serializers import BranchSerializer, CompanySerializer, TillSerializer


class MyCompanyView(RetrieveUpdateAPIView):
    """The signed-in user's own company. Any member can read it; only staff can edit name and address."""

    serializer_class = CompanySerializer
    permission_classes = [IsCompanyStaffOrReadOnly]

    def get_object(self):
        company = self.request.user.company
        if company is None:
            raise Http404('This account is not linked to a company.')
        return company


class BranchViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    permission_classes = [IsCompanyStaffOrReadOnly]


class TillViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Till.objects.all()
    serializer_class = TillSerializer
    permission_classes = [IsCompanyStaffOrReadOnly]
