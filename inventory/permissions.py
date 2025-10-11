# inventory/permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS

def in_group(user, name: str) -> bool:
    # case-insensitive just in case ("manager" vs "Manager")
    return user.is_authenticated and user.groups.filter(name__iexact=name).exists()

class ReadOnlyOrSuperAdmin(BasePermission):
    """
    - SAFE methods: any authenticated user can read.
    - Non-SAFE: ONLY superuser can create/update/delete.
    """
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)

    def has_object_permission(self, request, view, obj):
        # same rule at object level
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)
    