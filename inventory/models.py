# inventory/models.py
import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

class InventoryEntry(models.Model):
    # 🔁 Changed to match FE options
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_transit", "In Transit"),
        ("delivered", "Delivered"),
        ("rejected", "Rejected"),
    ]
    PAYMENT_CHOICES = [
        ("cash", "Cash"),
        ("transfer", "Transfer"),
        ("credit", "Credit"),
        ("other", "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField()

    customer_name = models.CharField(max_length=255, blank=True, null=True)
    mineral_or_equipment = models.CharField(max_length=255, blank=True, null=True)

    description = models.TextField(blank=True, null=True)
    supplier_agent = models.CharField(max_length=255, blank=True, null=True)
    truck_registration = models.CharField(max_length=80, db_index=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)

    driver_name = models.CharField(max_length=255, blank=True, null=True)
    driver_phone = models.CharField(max_length=50, blank=True, null=True)

    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.0"))
    unit = models.CharField(max_length=20, default="tons")

    origin = models.CharField(max_length=255, blank=True, null=True)
    destination = models.CharField(max_length=255, blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)

    transporter_name = models.CharField(max_length=255, blank=True, null=True)
    payment_type = models.CharField(max_length=20, choices=PAYMENT_CHOICES, blank=True, null=True)
    analysis_results = models.TextField(blank=True, null=True)
    gross_weight = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    tare_weight  = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    net_weight   = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    comment = models.TextField(blank=True, null=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   related_name="created_inventory", null=True, blank=True)
    modified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    related_name="modified_inventory", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["deleted", "date"]),
        ]

    def clean(self):
        # Normalize truck number
        if self.truck_registration:
            self.truck_registration = self.truck_registration.strip().upper()

        # Auto-compute net weight when possible
        if self.net_weight is None and self.gross_weight is not None and self.tare_weight is not None:
            self.net_weight = self.gross_weight - self.tare_weight

        # Non-negative checks
        for field in ("gross_weight", "tare_weight", "net_weight", "quantity"):
            val = getattr(self, field, None)
            if val is not None and val < 0:
                raise ValidationError({field: "Must be ≥ 0"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def soft_delete(self):
        self.deleted = True
        self.save()

class InventoryAttachment(models.Model):
    KIND_CHOICES = [("photo", "Photo"), ("spec", "Spec"), ("other", "Other")]

    entry = models.ForeignKey(InventoryEntry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="inventory/attachments/%Y/%m/")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, default="photo")
    mime_type = models.CharField(max_length=64, blank=True)
    size_kb = models.DecimalField(max_digits=10, decimal_places=1, default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=32, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["entry"]),
            models.Index(fields=["checksum"]),
        ]
        
class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(InventoryEntry, on_delete=models.CASCADE, related_name='audit_logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    changes = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']