from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from tenants.views import IsCompanyStaff
from vat.services import uk_date

from .dashboard import dashboard
from .serializers import DashboardQuerySerializer, DashboardSerializer


class DashboardView(APIView):
    """Sales, margin, waste and stock value for a week against the same days of the week before (G59). Staff only;
    the user's own company."""

    permission_classes = [IsCompanyStaff]

    @extend_schema(parameters=[OpenApiParameter('week', str, description='Any day in the week (YYYY-MM-DD); '
                                                                         'default this week.')],
                   responses=DashboardSerializer)
    def get(self, request):
        query = DashboardQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        today = uk_date()
        report = dashboard(request.user.company, query.validated_data.get('week') or today, today)
        return Response(DashboardSerializer(report).data)
