# procurement/urls.py
from rest_framework.routers import DefaultRouter
from .views import SupplierViewSet, LPOViewSet, GoodsReceiptViewSet

router = DefaultRouter()
router.register(r"suppliers", SupplierViewSet, basename="suppliers")
router.register(r"lpos", LPOViewSet, basename="lpos")
router.register(r"grn", GoodsReceiptViewSet, basename="grn")

urlpatterns = router.urls
