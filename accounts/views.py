# accounts/views.py
from django.contrib.auth import get_user_model
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse, extend_schema_view

from .permissions import IsSuperAdmin
from .serializers import (
    UserBaseSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    MeSerializer,
    SetPasswordSerializer,
    ChangeOwnPasswordSerializer,  # <-- make sure this exists in serializers.py
)

User = get_user_model()


COMMON_4XX = {
    400: OpenApiResponse(description="Bad Request"),
    401: OpenApiResponse(description="Unauthorized"),
    403: OpenApiResponse(description="Forbidden"),
    404: OpenApiResponse(description="Not Found"),
}

@extend_schema_view(
    list=extend_schema(summary="List users", responses={200: UserBaseSerializer(many=True), **COMMON_4XX}),
    retrieve=extend_schema(summary="Get user", responses={200: UserBaseSerializer, **COMMON_4XX}),
    destroy=extend_schema(summary="Delete user", responses={204: OpenApiResponse(description="No content"), **COMMON_4XX}),
)
@extend_schema(tags=["Users"])
class UserViewSet(viewsets.ModelViewSet):
    """
    Super Admin–only management of users.
    Also exposes:
      - GET  /api/users/me/                 : current user profile (any authenticated user)
      - POST /api/users/me/password/        : change own password (any authenticated user)
      - POST /api/users/{id}/set-password/  : set another user's password (superadmin only)
    """
    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = UserBaseSerializer
    permission_classes = [IsSuperAdmin]  # default for admin actions

    # ---- permissions ----
    def get_permissions(self):
        # Endpoints any authenticated user may hit
        if self.action in ("me", "change_own_password"):
            return [permissions.IsAuthenticated()]
        # Only superadmin can set someone else's password
        if self.action == "set_password":
            return [IsSuperAdmin()]
        # Fallback to default for admin CRUD
        return [perm() if isinstance(perm, type) else perm for perm in self.permission_classes]

    # ---- serializers ----
    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in ("update", "partial_update"):
            return UserUpdateSerializer
        if self.action == "me":
            return MeSerializer
        return UserBaseSerializer

    # ---- admin CRUD passthroughs (for explicit schema) ----
    @extend_schema(request=UserCreateSerializer, responses={201: UserBaseSerializer, **COMMON_4XX}, operation_id="users_create", summary="Create user",)
    def create(self, request, *args, **kwargs):
        ser = UserCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        # 🔁 respond with read serializer so FE gets role="manager" immediately
        out = UserBaseSerializer(user)
        headers = self.get_success_headers(out.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)
    
    @extend_schema(request=UserUpdateSerializer, responses={200: UserBaseSerializer, **COMMON_4XX}, operation_id="users_update", summary="Replace user",)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(request=UserUpdateSerializer, responses={200: UserBaseSerializer, **COMMON_4XX}, operation_id="users_partial_update", summary="Update user")
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    # ---------- Convenience: current user ----------
    @extend_schema(
        responses={200: MeSerializer, **COMMON_4XX},
        operation_id="users_me",
        description="Return the currently authenticated user.",
        summary="Get current user"
    )
    @action(detail=False, methods=["get"])
    def me(self, request):
        ser = MeSerializer(request.user)
        return Response(ser.data)

    # ---------- Self-service: change own password ----------
    @extend_schema(
        request=ChangeOwnPasswordSerializer,
        responses={200: OpenApiResponse(description="Password updated successfully"),**COMMON_4XX},
        operation_id="users_change_own_password",
        description="Authenticated user can change their own password by providing current and new passwords.",
        summary="Change pwn password"
    )
    @action(detail=False, methods=["post"], url_path="me/password")
    def change_own_password(self, request):
        ser = ChangeOwnPasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)

        user = request.user
        user.set_password(ser.validated_data["new_password"])
        user.save()
        return Response({"detail": "Password updated."}, status=status.HTTP_200_OK)

    # ---------- Admin: set password for a user ----------
    @extend_schema(
        request=SetPasswordSerializer,
        responses={200: OpenApiResponse(description="Password updated successfully"), **COMMON_4XX},
        operation_id="users_set_password",
        description="Super Admin can set a new password for a user.",
        summary="Set user password"
    )
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        user = self.get_object()
        ser = SetPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        user.set_password(ser.validated_data["password"])
        user.save()
        return Response({"detail": "Password updated."}, status=status.HTTP_200_OK)