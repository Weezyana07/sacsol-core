# inventory/admin.py

from django.contrib import admin
from .models import InventoryEntry, AuditLog

def _all_field_names(model):
    return [f.name for f in model._meta.get_fields() if getattr(f, "editable", False) or f.many_to_many]

@admin.register(InventoryEntry)
class InventoryEntryAdmin(admin.ModelAdmin):
    list_display = ("date","customer_name","mineral_or_equipment","truck_registration","quantity","status","created_at","deleted")
    list_filter = ("status","date","created_at","deleted")
    search_fields = ("customer_name","mineral_or_equipment","truck_registration","origin","destination","location","transporter_name")
    readonly_fields = ("created_at","updated_at")

    # Only superadmin can write
    def has_add_permission(self, request):       return bool(request.user and request.user.is_superuser)
    def has_change_permission(self, request, obj=None): return bool(request.user and request.user.is_superuser)
    def has_delete_permission(self, request, obj=None): return bool(request.user and request.user.is_superuser)

    # EITHER allow view-only for staff:
    def has_view_permission(self, request, obj=None):   return bool(request.user and request.user.is_authenticated)
    # OR, if you want to hide the model entirely from staff, use:
    # def has_view_permission(self, request, obj=None):   return bool(request.user and request.user.is_superuser)
    # def has_module_permission(self, request):           return bool(request.user and request.user.is_superuser)

    # Make everything read-only for non-superusers (no accidental edits through widgets)
    def get_readonly_fields(self, request, obj=None):
        if request.user and request.user.is_superuser:
            return super().get_readonly_fields(request, obj)
        return tuple(set(_all_field_names(InventoryEntry)) | set(self.readonly_fields))

    # Hide save/delete buttons for non-superusers
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = (extra_context or {})
        if not (request.user and request.user.is_superuser):
            extra_context.update({
                "show_save": False,
                "show_save_and_continue": False,
                "show_save_and_add_another": False,
                "show_delete": False,
                "show_close": True,
            })
        return super().changeform_view(request, object_id, form_url, extra_context)

    # Disable bulk actions for non-superusers
    def get_actions(self, request):
        return {} if not (request.user and request.user.is_superuser) else super().get_actions(request)

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("entry","user","action","timestamp")
    list_filter = ("action","timestamp")
    search_fields = ("entry__truck_registration","user__username","action")

    # Keep audit logs restricted
    def has_module_permission(self, request):            return bool(request.user and request.user.is_superuser)
    def has_view_permission(self, request, obj=None):    return bool(request.user and request.user.is_superuser)
    def has_add_permission(self, request):               return False
    def has_change_permission(self, request, obj=None):  return False
    def has_delete_permission(self, request, obj=None):  return False