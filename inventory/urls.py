# # inventory/urls.py
# from rest_framework.routers import DefaultRouter
# from .views import InventoryEntryViewSet, InventoryViewSet, AuditLogViewSet

# router = DefaultRouter(trailing_slash=False)  # ⬅️ important
# router.register(r"inventory", InventoryViewSet, basename="inventory")
# router.register(r"audit-logs", AuditLogViewSet, basename="audit-logs")
# router.register(r"inventory-entries", InventoryEntryViewSet, basename="inventoryentry")
# urlpatterns = router.urls

# inventory/urls.py
from rest_framework.routers import DefaultRouter
from .views import InventoryEntryViewSet, InventoryViewSet, AuditLogViewSet

router = DefaultRouter()
router.trailing_slash = '/?'          # accept both / and no /
router.register(r"inventory", InventoryViewSet, basename="inventory")
router.register(r"audit-logs", AuditLogViewSet, basename="audit-logs")
router.register(r"inventory-entries", InventoryEntryViewSet, basename="inventoryentry")
urlpatterns = router.urls