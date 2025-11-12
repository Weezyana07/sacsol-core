# sacsol/urls.py
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from core.health import HealthView
from core.jwt_views import PublicTokenObtainPairView, PublicTokenRefreshView
from core.audit_api import AuditLogsList

urlpatterns = [
    path("admin/", admin.site.urls),

    # Public health & auth
    re_path(r"^api/health/?$", HealthView.as_view(), name="health"),
    re_path(r"^api/auth/token/?$", PublicTokenObtainPairView.as_view(), name="token_obtain_pair"),
    re_path(r"^api/auth/token/refresh/?$", PublicTokenRefreshView.as_view(), name="token_refresh"),

    # OpenAPI & Docs
    re_path(r"^api/schema/?$", SpectacularAPIView.as_view(), name="schema"),
    re_path(r"^api/docs/?$", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    re_path(r"^api/redoc/?$", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),

    # *** Force unified audit endpoint to take precedence ***
    re_path(r"^api/audit-logs/?$", AuditLogsList.as_view(), name="audit-logs"),

    # App routers (order no longer matters for audit-logs)
    path("api/", include("inventory.urls")),
    path("api/", include("accounts.urls")),
    path("api/", include("procurement.urls")),
    path("api/", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)