# # procurement/urls.py
# from rest_framework.routers import DefaultRouter
# from .views import SupplierViewSet, LPOViewSet, GoodsReceiptViewSet

# router = DefaultRouter(trailing_slash=False)  # ⬅️
# router.register(r"suppliers", SupplierViewSet, basename="suppliers")
# router.register(r"lpos", LPOViewSet, basename="lpos")
# router.register(r"grn", GoodsReceiptViewSet, basename="grn")
# urlpatterns = router.urls

# procurement/urls.py
from rest_framework.routers import DefaultRouter
from .views import SupplierViewSet, LPOViewSet, GoodsReceiptViewSet

router = DefaultRouter()
router.trailing_slash = '/?'          # accept both / and no /
router.register(r"suppliers", SupplierViewSet, basename="suppliers")
router.register(r"lpos", LPOViewSet, basename="lpos")
router.register(r"grn", GoodsReceiptViewSet, basename="grn")
urlpatterns = router.urls