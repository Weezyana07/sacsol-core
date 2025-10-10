# core/health.py
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes

class HealthView(APIView):
    authentication_classes = []   # public
    permission_classes = []       # public

    @extend_schema(
        tags=["Health"],
        operation_id="health_check",
        responses={200: OpenApiTypes.OBJECT},
        description="Lightweight liveness check."
    )
    def get(self, request):
        return Response({"status": "ok"})