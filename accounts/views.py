# accounts/views.py
from django.contrib.auth import get_user_model
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse
from drf_spectacular.types import OpenApiTypes

from .permissions import IsSuperAdmin
from .serializers import (
    UserBaseSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    MeSerializer,
    SetPasswordSerializer,
)

User = get_user_model()

@extend_schema(tags=["Users"])
class UserViewSet(viewsets.ModelViewSet):
    """
    Super Admin–only management of users.
    Also exposes a `/me/` endpoint for any authenticated user.
    """
    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = UserBaseSerializer
    permission_classes = [IsSuperAdmin]  # default for admin actions

    def get_queryset(self):
        # Optionally hide superusers from non-superadmin—here only superadmin can access anyway.
        return super().get_queryset()

    def get_permissions(self):
        # Allow any authenticated user to hit /me/
        if self.action == "me":
            return [permissions.IsAuthenticated()]
        if self.action == "set_password":
            # Only superadmin can change others' passwords through this admin endpoint
            return [IsSuperAdmin()]
        return [perm() if isinstance(perm, type) else perm for perm in self.permission_classes]

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in ("update", "partial_update"):
            return UserUpdateSerializer
        if self.action == "me":
            return MeSerializer
        return UserBaseSerializer

    @extend_schema(
        request=UserCreateSerializer,
        responses={201: UserBaseSerializer},
        operation_id="users_create",
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(
        request=UserUpdateSerializer,
        responses={200: UserBaseSerializer},
        operation_id="users_update",
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(
        request=UserUpdateSerializer,
        responses={200: UserBaseSerializer},
        operation_id="users_partial_update",
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    # ---------- Convenience: current user ----------
    @extend_schema(
        responses={200: MeSerializer},
        operation_id="users_me",
        description="Return the currently authenticated user.",
    )
    @action(detail=False, methods=["get"])
    def me(self, request):
        ser = MeSerializer(request.user)
        return Response(ser.data)

    # ---------- Admin: set password for a user ----------
    @extend_schema(
        request=SetPasswordSerializer,
        responses={200: OpenApiResponse(description="Password updated successfully")},
        operation_id="users_set_password",
        description="Super Admin can set a new password for a user.",
    )
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        user = self.get_object()
        ser = SetPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user.set_password(ser.validated_data["password"])
        user.save()
        return Response({"detail": "Password updated."}, status=status.HTTP_200_OK)