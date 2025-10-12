# procurement/admin.py
from django.contrib import admin
from .models import Supplier, LPO, LPOItem, LPOAttachment, GoodsReceipt, GoodsReceiptItem

class LPOItemInline(admin.TabularInline):
    model = LPOItem
    extra = 0
    readonly_fields = ("line_total",)

class LPOAttachmentInline(admin.TabularInline):
    model = LPOAttachment
    extra = 0
    readonly_fields = ("mime_type", "size_kb", "width", "height", "checksum", "uploaded_at")

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("supplier_code", "name", "phone", "email", "is_active")
    list_filter = ("is_active",)
    search_fields = ("supplier_code", "name", "phone", "email", "rc_number", "tax_id")
    readonly_fields = ("supplier_code",)

@admin.register(LPO)
class LPOAdmin(admin.ModelAdmin):
    list_display = ("lpo_number", "supplier", "status", "currency", "grand_total", "created_at", "approved_at")
    list_filter = ("status", "currency", "created_at", "approved_at")
    search_fields = ("lpo_number", "supplier__name")
    readonly_fields = ("lpo_number", "subtotal", "grand_total", "created_by", "approved_by", "submitted_at", "approved_at", "created_at", "updated_at")
    inlines = [LPOItemInline, LPOAttachmentInline]
    actions = ["mark_submitted", "mark_approved", "mark_cancelled"]

    @admin.action(description="Submit selected LPOs")
    def mark_submitted(self, request, queryset):
        for lpo in queryset:
            if lpo.status == LPO.STATUS_DRAFT:
                lpo.submit(request.user); lpo.save(update_fields=["status","submitted_at"])

    @admin.action(description="Approve selected LPOs")
    def mark_approved(self, request, queryset):
        for lpo in queryset:
            if lpo.status == LPO.STATUS_SUBMITTED:
                lpo.approve(request.user); lpo.save(update_fields=["status","approved_at","approved_by"])

    @admin.action(description="Cancel selected LPOs")
    def mark_cancelled(self, request, queryset):
        for lpo in queryset:
            if lpo.status not in (LPO.STATUS_CANCELLED, LPO.STATUS_FULFILLED):
                lpo.cancel(request.user); lpo.save(update_fields=["status"])

class GoodsReceiptItemInline(admin.TabularInline):
    model = GoodsReceiptItem
    extra = 0

@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "lpo", "received_by", "received_at", "reference")
    list_filter = ("received_at",)
    search_fields = ("lpo__lpo_number", "reference", "lpo__supplier__name")
    inlines = [GoodsReceiptItemInline]
