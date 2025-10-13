from rest_framework.permissions import BasePermission, SAFE_METHODS

def in_group(user, *names: str) -> bool:
    return user.is_authenticated and user.groups.filter(
        name__iregex="^(" + "|".join(names) + ")$"
    ).exists()

class InventoryWritePolicy(BasePermission):
    """
    Read: any authenticated user.
    Create (POST): superuser OR in Manager/Staff group.
    Update/Delete (PUT/PATCH/DELETE): superuser only.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        if request.method == "POST":
            return request.user.is_superuser or in_group(request.user, "Manager", "Staff")
        return request.user.is_superuser

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)