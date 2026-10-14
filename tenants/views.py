# tenants/views.py

from django.contrib.auth import get_user_model
from django.http import Http404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.response import Response

from authentication import refresh_tokens
from common.tenancy import IsCompanyStaffOrReadOnly, TenantScopedMixin

from .models import Branch, Till
from .serializers import BranchSerializer, CompanySerializer, OperatorSerializer, TillSerializer


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


class IsCompanyStaff(IsCompanyStaffOrReadOnly):
    """Staff (or superusers) only, for reading as well as writing."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.company_id and (user.is_staff or user.is_superuser))


OperatorWithCode = inline_serializer(
    'OperatorWithCode',
    {
        'id': serializers.IntegerField(),
        'name': serializers.CharField(),
        'role': serializers.CharField(),
        'is_active': serializers.BooleanField(),
        'code': serializers.CharField(help_text='The 8-digit sign-in code. Shown this once; it is not stored.'),
    },
)


class OperatorViewSet(
    TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """
    Operators (till, demand, freight, supplier, stock), who sign in with an 8-digit code (staff only).

    Creating one (or resetting its code) generates the code and returns it once: write it down, it cannot be
    looked up later. Operators are switched off with ``is_active`` rather than deleted, so their work stays
    attributed to them. The role can be changed with PATCH.
    """

    queryset = get_user_model().objects.exclude(role='').order_by('name', 'id')
    serializer_class = OperatorSerializer
    permission_classes = [IsCompanyStaff]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    @extend_schema(responses={201: OperatorWithCode})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, code = get_user_model().objects.create_operator(
            request.user.company, serializer.validated_data['name'], serializer.validated_data['role']
        )
        return Response({**OperatorSerializer(user).data, 'code': code}, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        user = serializer.save()
        if not user.is_active or 'role' in serializer.validated_data:
            # A changed role must not keep working on the access the old one had.
            refresh_tokens.revoke_all_for_user(user)

    @extend_schema(request=None, responses=OperatorWithCode)
    @action(detail=True, methods=['post'], url_path='reset-code')
    def reset_code(self, request, pk=None):
        """Replace the operator's code (for a forgotten or leaked one). Their current session is ended."""
        user = self.get_object()
        code = get_user_model().objects.reset_operator_code(user)
        refresh_tokens.revoke_all_for_user(user)
        return Response({**OperatorSerializer(user).data, 'code': code})
