from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.tenancy import TenantScopedMixin
from inventory.views import CompanyMemberReadsStaffWrites
from vat.services import uk_date

from .models import Promotion
from .serializers import PromotionSerializer

STATUSES = (Promotion.SCHEDULED, Promotion.LIVE, Promotion.ENDED)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, enum=list(STATUSES)), OpenApiParameter('kind', str),
]))
class PromotionViewSet(TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
                       mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Till promotions (G58). Members read; staff create, edit and end them. Never deleted: sales point at them."""

    queryset = Promotion.objects.prefetch_related('products', 'categories', 'branches', 'groups__products')
    serializer_class = PromotionSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset().filter(company_id=self.request.user.company_id)
        if self.action != 'list':
            return queryset
        today, status = uk_date(), self.request.query_params.get('status')
        ended = Q(is_active=False) | Q(ends_on__lt=today)
        if status == Promotion.ENDED:
            queryset = queryset.filter(ended)
        elif status == Promotion.SCHEDULED:
            queryset = queryset.exclude(ended).filter(starts_on__gt=today)
        elif status == Promotion.LIVE:
            queryset = queryset.exclude(ended).filter(starts_on__lte=today)
        elif status:
            raise ValidationError({'status': f'One of {", ".join(STATUSES)}.'})
        if self.request.query_params.get('kind'):
            queryset = queryset.filter(kind=self.request.query_params['kind'])
        return queryset

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company, created_by=self.request.user)

    @extend_schema(request=None, responses=PromotionSerializer)
    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        """End it now: switched off, and its end date set to today if it ran later (or had none)."""
        with transaction.atomic():
            promotion = Promotion.objects.select_for_update().get(pk=self.get_object().pk)
            today = uk_date()
            if promotion.status(today) == Promotion.ENDED:
                raise ValidationError({'detail': 'This promotion has already ended.'})
            promotion.is_active = False
            if promotion.ends_on is None or promotion.ends_on > today:
                promotion.ends_on = max(today, promotion.starts_on)
            promotion.save(update_fields=['is_active', 'ends_on', 'updated_at'])
        return Response(self.get_serializer(promotion).data)
