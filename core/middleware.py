# core/middleware.py
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

class BlockInventoryWritesForNonSuperuser(MiddlewareMixin):
    """
    Enforce superuser-only writes in Django Admin.
    DRF-based API is handled by DRF permissions (JWT-aware), so we skip /api/* here.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        path = request.path

        # Skip API entirely — DRF will enforce permissions with JWT
        if path.startswith("/api/"):
            return None

        # Lock down admin edits to InventoryEntry via session auth
        if path.startswith("/admin/inventory/inventoryentry"):
            user = getattr(request, "user", None)
            if request.method in WRITE_METHODS and not (user and user.is_authenticated and user.is_superuser):
                return JsonResponse({"detail": "Only superadmin can modify inventory (admin)."}, status=403)

        return None