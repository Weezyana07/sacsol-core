# core/health.py
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse
from drf_spectacular.types import OpenApiTypes

class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        auth=[],
        tags=["Health"],
        summary="Liveness check",
        operation_id="health_check",
        responses={
            200: OpenApiTypes.OBJECT,
            400: OpenApiResponse(description="Bad Request"),   # ← add this (or any 4XX)
        },
        description="Lightweight liveness check."
    )
    def get(self, request):
        return Response({"status": "ok"})