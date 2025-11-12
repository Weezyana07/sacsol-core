# inventory/permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS
from core.roles import is_owner, is_staff_or_manager_or_owner, in_groups

def in_group(user, *names: str) -> bool:
    # keep a short alias for views that already import `in_group` from inventory.permissions
    return in_groups(user, *names)

class InventoryWritePolicy(BasePermission):
    """
    Read (SAFE): any authenticated user.
    Create (POST): superuser OR Manager/Staff.
    Update/Delete (PUT/PATCH/DELETE): superuser only.
    """
    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        if request.method == "POST":
            return user.is_superuser or in_group(user, "Manager", "Staff")
        return user.is_superuser

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)

class InventoryListOwnerOnly(BasePermission):
    """
    For list/read endpoints: only Owner (superuser) may read.
    """
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return is_owner(getattr(request, "user", None))
        # writes should use a different policy; block here
        return False

class InventoryCreatePolicy(BasePermission):
    """
    Allow POST (create) for Owner/Manager/Staff.
    Block GET/HEAD/OPTIONS here; reads are handled by the list viewset with stricter rules.
    """
    def has_permission(self, request, view):
        if request.method == "POST":
            return is_staff_or_manager_or_owner(getattr(request, "user", None))
        return False

# class InventoryNewAllowed(BasePermission):
#     def has_permission(self, request, view):
#         # Allow authenticated staff/manager/owner to hit /api/inventory/new/
#         return bool(request.user and request.user.is_authenticated and is_staff_or_manager_or_owner(request.user))
    