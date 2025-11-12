# core/jwt_views.py
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.utils import extend_schema, OpenApiResponse
from drf_spectacular.types import OpenApiTypes


class PublicTokenObtainPairView(TokenObtainPairView):
    @extend_schema(
        auth=[], tags=["Auth"], summary="Obtain JWT access/refresh tokens",
        operation_id="auth_token_create",
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiResponse(description="Bad Request"), 401: OpenApiResponse(description="Unauthorized")},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class PublicTokenRefreshView(TokenRefreshView):
    @extend_schema(
        auth=[], tags=["Auth"], summary="Refresh JWT access token",
        operation_id="auth_token_refresh_create",
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiResponse(description="Bad Request"), 401: OpenApiResponse(description="Unauthorized")},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)