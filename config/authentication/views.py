from rest_framework import viewsets, generics
from django.db.models import Q

class TenantModelViewSet(viewsets.ModelViewSet):
    """
    Base ViewSet class for handling multi-tenant models.

    This class overrides the `get_queryset` method to filter objects by the current company.
    """

    def get_queryset(self):
        """
        Override the default queryset to filter by the request's associated company.

        Returns:
            QuerySet: Filtered queryset based on the request's company.
        """
        user = self.request.user
        if hasattr(user, 'company'):
            return self.queryset.filter(Q(company=user.company))
        else:
            return self.queryset.none()