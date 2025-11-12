# procurement/models.py
from __future__ import annotations
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class Supplier(models.Model):
    supplier_code = models.CharField(max_length=32, unique=True, db_index=True)  # e.g., SUP-2025-000123
    name = models.CharField(max_length=255)  # NOT unique
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True)
    rc_number = models.CharField(max_length=64, blank=True)
    tax_id = models.CharField(max_length=64, blank=True)
    contact_person = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["phone"]),
            models.Index(fields=["email"]),
            models.Index(fields=["rc_number"]),
            models.Index(fields=["tax_id"]),
        ]

    def __str__(self): return f"{self.name} · {self.supplier_code}"


class LPOSequence(models.Model):
    """
    Global (single-tenant) LPO counter with yearly reset.
    One row per year.
    """
    year = models.IntegerField(default=timezone.now().year, unique=True)
    counter = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.year} / {self.counter}"


class LPO(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_PARTIAL = "partially_received"
    STATUS_FULFILLED = "fulfilled"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PARTIAL, "Partially Received"),
        (STATUS_FULFILLED, "Fulfilled"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="lpos")
    lpo_number = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    currency = models.CharField(max_length=8, default="NGN")
    delivery_address = models.TextField(blank=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=255, blank=True)

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="lpos_created")
    approved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="lpos_approved")
    submitted_by = models.ForeignKey(        # ← NEW
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="lpos_submitted"
    )
    # simple soft-delete flag (kept from your draft)
    deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return f"{self.lpo_number} · {self.supplier.name}"

    # ---- business helpers ----
    @property
    def is_editable(self) -> bool:
        return self.status in {self.STATUS_DRAFT, self.STATUS_SUBMITTED}

    def recompute_totals(self) -> None:
        subtotal = sum((li.line_total for li in self.items.all()), Decimal("0.00"))
        self.subtotal = subtotal
        self.grand_total = subtotal + self.tax_amount - self.discount_amount

    def refresh_receive_status(self) -> None:
        total_ordered = sum((i.qty for i in self.items.all()), Decimal("0"))
        total_received = sum((i.total_received for i in self.items.all()), Decimal("0"))
        if total_received == 0:
            return
        if total_received >= total_ordered:
            self.status = self.STATUS_FULFILLED
        else:
            self.status = self.STATUS_PARTIAL

    def submit(self, user) -> None:
        if self.status != self.STATUS_DRAFT:
            raise ValueError("Only draft LPO can be submitted.")
        if self.items.count() == 0 or self.grand_total <= 0:
            raise ValueError("Cannot submit without items and totals.")
        self.status = self.STATUS_SUBMITTED
        self.submitted_by = user                 # ← NEW
        self.submitted_at = timezone.now()

    def approve(self, user) -> None:
        if self.status != self.STATUS_SUBMITTED:
            raise ValueError("Only submitted LPO can be approved.")
        if self.items.count() == 0 or self.grand_total <= 0:
            raise ValueError("Cannot approve an empty LPO.")
        self.status = self.STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()

    def cancel(self, user) -> None:
        if self.status in {self.STATUS_FULFILLED, self.STATUS_CANCELLED}:
            raise ValueError("Cannot cancel a fulfilled or already cancelled LPO.")
        self.status = self.STATUS_CANCELLED


class LPOItem(models.Model):
    lpo = models.ForeignKey(LPO, on_delete=models.CASCADE, related_name="items")
    inventory_item = models.ForeignKey(
        "inventory.InventoryEntry",
        on_delete=models.PROTECT,
        null=True, blank=True,   # <-- make optional
        related_name="lpo_items",
    )
    description = models.CharField(max_length=255, blank=True)
    qty = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        indexes = [models.Index(fields=["lpo", "inventory_item"])]

    def save(self, *args, **kwargs):
        self.line_total = (self.qty or 0) * (self.unit_price or 0)
        super().save(*args, **kwargs)

    @property
    def total_received(self) -> Decimal:
        agg = self.receipts.aggregate(s=models.Sum("qty_received"))["s"]
        return agg or Decimal("0")


class LPOAttachment(models.Model):
    KIND_CHOICES = [("quotation", "Quotation"), ("spec", "Spec"), ("other", "Other")]
    lpo = models.ForeignKey(LPO, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="procurement/lpo/%Y/%m/")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, default="other")
    mime_type = models.CharField(max_length=64, blank=True)
    size_kb = models.DecimalField(max_digits=10, decimal_places=1, default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=32, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class GoodsReceipt(models.Model):
    lpo = models.ForeignKey(LPO, on_delete=models.PROTECT, related_name="grns")
    received_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="grns_received")
    received_at = models.DateTimeField(default=timezone.now)
    reference = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)


class GoodsReceiptItem(models.Model):
    grn = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="items")
    lpo_item = models.ForeignKey(LPOItem, on_delete=models.PROTECT, related_name="receipts")
    qty_received = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        indexes = [models.Index(fields=["grn", "lpo_item"])]

    def clean(self):
        from django.core.exceptions import ValidationError
        remaining = self.lpo_item.qty - self.lpo_item.total_received
        if self.qty_received > remaining:
            raise ValidationError(f"Qty exceeds remaining ({remaining}).")

class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="procurement_audit_logs",  # <-- add this
    )
    verb = models.CharField(max_length=64)
    lpo = models.ForeignKey(LPO, null=True, blank=True, on_delete=models.CASCADE)
    grn = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.CASCADE)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)