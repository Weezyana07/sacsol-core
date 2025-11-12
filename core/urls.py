# core/urls.py 
from django.urls import path
from core.audit_api import AuditLogsList

urlpatterns = [
    path("audit-logs/", AuditLogsList.as_view(), name="audit-logs"),  # replaces old single-source endpoint
]