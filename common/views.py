# common/views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import DemandAlert

class DismissDemandAlertView(APIView):
    """
    View for dismissing a demand alert.

    This view handles the PATCH request to mark a specific demand alert as dismissed.
    Only authenticated users can perform this action.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, format=None):
        """
        Patch request handler to dismiss a demand alert.

        Parameters:
        - request: The HTTP request object.
        - pk: The primary key of the DemandAlert to be dismissed.
        - format: (Optional) The format of the response.

        Returns:
        - A Response object containing the result of the operation.
        """
        try:
            # Retrieve the demand alert by its primary key
            demand_alert = DemandAlert.objects.get(pk=pk)

            # Check if the user is allowed to dismiss this alert
            if not self.request.user.owner.branch == demand_alert.branch:
                return Response({"detail": "You do not have permission to dismiss this alert."}, status=403)

            # Mark the demand alert as dismissed
            demand_alert.dismissed = True
            demand_alert.save()

            # Return a success response
            return Response({"detail": "Demand alert dismissed successfully."}, status=200)
        except DemandAlert.DoesNotExist:
            # Return an error response if the demand alert does not exist
            return Response({"detail": "Demand alert not found."}, status=404)