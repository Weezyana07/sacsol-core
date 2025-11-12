# # accounts/urls.py
# from rest_framework.routers import DefaultRouter
# from .views import UserViewSet

# router = DefaultRouter(trailing_slash=False)  # ⬅️
# router.register(r"", UserViewSet, basename="users")
# urlpatterns = router.urls

# accounts/urls.py
from rest_framework.routers import DefaultRouter
from .views import UserViewSet

router = DefaultRouter()
router.trailing_slash = '/?'            # accept both with/without slash
router.register(r"users", UserViewSet, basename="users")   # <-- non-empty prefix
urlpatterns = router.urls