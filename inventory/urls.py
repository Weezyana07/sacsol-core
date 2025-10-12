# inventory/urls.py
from rest_framework.routers import DefaultRouter
from .views import InventoryEntryViewSet, InventoryViewSet, AuditLogViewSet

router = DefaultRouter()
router.register(r"inventory", InventoryViewSet, basename="inventory")
router.register(r"audit-logs", AuditLogViewSet, basename="audit-logs")  # NEW
router.register(r"inventory-entries", InventoryEntryViewSet, basename="inventoryentry")

urlpatterns = router.urls