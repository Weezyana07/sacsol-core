# accounts/permissions.py
from rest_framework.permissions import BasePermission

class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
    