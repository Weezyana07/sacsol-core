# inventory/permissions.py
from rest_framework import permissions

def in_group(user, name: str) -> bool:
    return user.is_authenticated and user.groups.filter(name=name).exists()

class IsOwnerOrManager(permissions.BasePermission):
    """
    - SAFE methods: require authentication to read.
    - Non-SAFE: superuser or Manager group can edit; otherwise only the owner (created_by).
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        if request.user.is_superuser or in_group(request.user, "Manager"):
            return True
        return getattr(obj, "created_by_id", None) == getattr(request.user, "id", None)