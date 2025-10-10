# inventory/admin.py
from django.contrib import admin
from .models import InventoryEntry, AuditLog

@admin.register(InventoryEntry)
class InventoryEntryAdmin(admin.ModelAdmin):
    list_display = ("date","customer_name","mineral_or_equipment","truck_registration","quantity","status","created_at","deleted")
    list_filter = ("status","date","created_at","deleted")
    search_fields = ("customer_name","mineral_or_equipment","truck_registration","origin","destination","location","transporter_name")
    readonly_fields = ("created_at","updated_at")

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("entry","user","action","timestamp")
    list_filter = ("action","timestamp")
    search_fields = ("entry__truck_registration","user__username","action")