# procurement/views.py
from __future__ import annotations

import hashlib
from io import BytesIO
from django.db import models
from django.conf import settings
from django.core.files.base import ContentFile

from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiTypes, OpenApiResponse
from PIL import Image, UnidentifiedImageError, ImageFile  # Pillow
from rest_framework import permissions, renderers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.views import COMMON_4XX

from .models import (
    Supplier,
    LPO,
    LPOAttachment,
    GoodsReceipt,
    AuditLog,
    LPOSequence,  # yearly counter
)
from .permissions import LPOReadPolicy, LPOWritePolicy
from core.roles import is_manager_or_owner, in_groups, is_owner
from .serializers import (
    SupplierSerializer,
    LPOSerializer,
    LPOAttachmentSerializer,
    GoodsReceiptSerializer,
)
from .services import render_lpo_pdf_bytes

ImageFile.LOAD_TRUNCATED_IMAGES = True  # tolerate truncated streams safely


class PDFRenderer(renderers.BaseRenderer):
    media_type = "application/pdf"
    format = "pdf"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


# ---------- Supplier ----------
@extend_schema_view(
    list=extend_schema(tags=["Procurement / Suppliers"],  operation_id="supplier_list",           summary="List suppliers",
                       responses={200: SupplierSerializer(many=True), **COMMON_4XX}),
    retrieve=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_retrieve",    summary="Get supplier",
                           responses={200: SupplierSerializer, **COMMON_4XX}),
    create=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_create",        summary="Create supplier",
                         responses={201: SupplierSerializer, **COMMON_4XX}),
    update=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_update",        summary="Replace supplier",
                         responses={200: SupplierSerializer, **COMMON_4XX}),
    partial_update=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_partial_update", summary="Update supplier",
                                 responses={200: SupplierSerializer, **COMMON_4XX}),
    destroy=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_destroy",      summary="Delete supplier",
                          responses={204: OpenApiResponse(description="No content"), **COMMON_4XX}),
)
class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(name__icontains=q)
        return qs


# ---------- LPO ----------
@extend_schema_view(
    list=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_list", summary="List LPOs",
                       responses={200: LPOSerializer(many=True), **COMMON_4XX}),
    retrieve=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_retrieve", summary="Get LPO",
                           responses={200: LPOSerializer, **COMMON_4XX}),
    create=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_create", summary="Create LPO",
                         responses={201: LPOSerializer, **COMMON_4XX}),
    update=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_update", summary="Replace LPO",
                         responses={200: LPOSerializer, **COMMON_4XX}),
    partial_update=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_partial_update", summary="Update LPO",
                                 responses={200: LPOSerializer, **COMMON_4XX}),
    destroy=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_destroy", summary="Delete LPO",
                          responses={204: OpenApiResponse(description="No content"), **COMMON_4XX}),
)
class LPOViewSet(viewsets.ModelViewSet):
    queryset = (
        LPO.objects.select_related("supplier", "approved_by", "created_by", "submitted_by")
        .prefetch_related("items", "attachments")
        .all()
    )
    serializer_class = LPOSerializer
    # READ is allowed per-object by LPOReadPolicy; writes checked below with LPOWritePolicy
    permission_classes = [permissions.IsAuthenticated, LPOReadPolicy]

    def get_permissions(self):
        if self.action == "approve":
            from rest_framework.permissions import BasePermission
            class _MgrOrOwner(BasePermission):
                def has_permission(self, request, view):
                    return is_manager_or_owner(request.user)
            return [permissions.IsAuthenticated(), _MgrOrOwner()]

        if self.action == "cancel":
            from rest_framework.permissions import BasePermission
            class _SuperOnly(BasePermission):
                def has_permission(self, request, view):
                    return bool(getattr(request.user, "is_superuser", False))
            return [permissions.IsAuthenticated(), _SuperOnly()]

        return [perm() if isinstance(perm, type) else perm for perm in self.permission_classes]

    # ---- filters / scope ----
    def get_queryset(self):
        qs = (
            LPO.objects.select_related("supplier", "approved_by", "created_by", "submitted_by")
            .prefetch_related("items", "attachments")
            .filter(deleted=False)
            .order_by("-created_at")
        )
    
        # Scope: managers/owners see all; staff only their own
        if not is_manager_or_owner(self.request.user):
            qs = qs.filter(created_by=self.request.user)
    
        # Filters
        status_q = self.request.query_params.get("status")
        supplier = self.request.query_params.get("supplier")
        q = self.request.query_params.get("q")
    
        if status_q:
            qs = qs.filter(status=status_q)
        if supplier:
            qs = qs.filter(supplier_id=supplier)
        if q:
            qs = qs.filter(
                models.Q(lpo_number__icontains=q) |
                models.Q(supplier__name__icontains=q) |
                models.Q(submitted_by__username__icontains=q)
            )
    
        return qs

    # ---- create: set creator + yearly sequence number ----
    def perform_create(self, serializer):
        from django.utils import timezone
        from django.db import transaction
        with transaction.atomic():
            year = timezone.now().year
            seq, _ = LPOSequence.objects.select_for_update().get_or_create(year=year)
            seq.counter += 1
            seq.save(update_fields=["counter"])
            number = f"LPO-{year}-{seq.counter:06d}"

            lpo = serializer.save(created_by=self.request.user, lpo_number=number)
            lpo.recompute_totals()
            lpo.save(update_fields=["subtotal", "grand_total"])

            AuditLog.objects.create(
                actor=self.request.user,
                verb="create",
                lpo=lpo,
                payload={
                    "supplier": getattr(lpo.supplier, "name", None),
                    "subtotal": str(lpo.subtotal),
                    "grand_total": str(lpo.grand_total),
                },
            )
    # ---- safe reads (object-level LPOReadPolicy already applied) ----
    # (DRF's retrieve uses get_queryset + LPOReadPolicy, so no override is strictly required.)

    # ---- updates: enforce LPOWritePolicy per object ----
    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        if not LPOWritePolicy().has_object_permission(request, self, obj):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You cannot modify this LPO.")

        response = super().update(request, *args, **kwargs)

        # ⬇️ NEW: write audit
        try:
            # prefer validated data if serializer is present
            payload = getattr(self, "serializer_class", None)
            payload = request.data  # simple + robust
            AuditLog.objects.create(
                actor=request.user,
                verb="update",
                lpo=obj,
                payload=payload,
            )
        except Exception:
            pass

        return response

    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()
        if not LPOWritePolicy().has_object_permission(request, self, obj):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You cannot modify this LPO.")

        response = super().partial_update(request, *args, **kwargs)

        # ⬇️ NEW: write audit
        try:
            AuditLog.objects.create(
                actor=request.user,
                verb="update",
                lpo=obj,
                payload=request.data,
            )
        except Exception:
            pass

        return response
    # ---- actions ----
    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_submit", summary="Submit LPO",
                   responses={200: OpenApiTypes.OBJECT, **COMMON_4XX})
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        lpo = self.get_object()
        if not LPOWritePolicy().has_object_permission(request, self, lpo):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You cannot submit this LPO.")
        if lpo.status != "draft":
            return Response({"detail": "Only draft LPO can be submitted."}, status=status.HTTP_400_BAD_REQUEST)

        from django.utils import timezone
        if hasattr(lpo, "submit"):
            lpo.submit(request.user)  # should set submitted_at
            lpo.submitted_by = request.user
            lpo.save(update_fields=["status", "submitted_by", "submitted_at"])
        else:
            lpo.status = "submitted"
            lpo.submitted_by = request.user
            lpo.submitted_at = timezone.now()
            lpo.save(update_fields=["status", "submitted_by", "submitted_at"])

        AuditLog.objects.create(actor=request.user, verb="submitted", lpo=lpo)
        return Response({"status": lpo.status})
    
    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_approve", summary="Approve LPO",
                   responses={200: OpenApiTypes.OBJECT, **COMMON_4XX})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        # get_permissions() already restricted this to Manager/Owner
        lpo = self.get_object()
        try:
            if hasattr(lpo, "approve"):
                lpo.approve(request.user)
            else:
                lpo.status = "approved"
                if hasattr(lpo, "approved_by"):
                    lpo.approved_by = request.user
                if hasattr(lpo, "approved_at"):
                    lpo.approved_at = timezone.now()
            fields = ["status", "approved_at", "approved_by"] if hasattr(lpo, "approved_at") else ["status", "approved_by"]
            lpo.save(update_fields=[f for f in fields if hasattr(lpo, f)])
            AuditLog.objects.create(actor=request.user, verb="approved", lpo=lpo)
            return Response({"status": lpo.status})
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_cancel", summary="Cancel LPO",
                   responses={200: OpenApiTypes.OBJECT, **COMMON_4XX})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        lpo = self.get_object()
        if not getattr(request.user, "is_superuser", False):
            raise PermissionDenied("Only super admin can cancel LPOs.")
        try:
            if hasattr(lpo, "cancel"):
                lpo.cancel(request.user)
            else:
                lpo.status = "cancelled"
            lpo.save(update_fields=["status"])
            AuditLog.objects.create(actor=request.user, verb="cancelled", lpo=lpo)
            return Response({"status": lpo.status})
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # ---------- Attachments (GET list + POST upload) ----------
    @extend_schema(
        methods=["get"],
        tags=["Procurement / LPO Attachments"],
        summary="List LPO attachments",
        operation_id="lpo_attachments_list",
        responses={200: LPOAttachmentSerializer(many=True), **COMMON_4XX},  # ← add
    )
    @extend_schema(
        methods=["post"],
        tags=["Procurement / LPO Attachments"],
        operation_id="lpo_attachments_create",
        summary="Upload LPO attachment",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                    "kind": {"type": "string", "enum": ["quote", "invoice", "other"]},
                },
                "required": ["file"],
            }
        },
        responses={201: LPOAttachmentSerializer, **COMMON_4XX},            # ← add
    )
    @action(detail=True, methods=["get", "post"], url_path="attachments")
    def attachments(self, request, pk=None):
        lpo = self.get_object()

        # GET → list
        if request.method == "GET":
            qs = lpo.attachments.all().order_by("-uploaded_at")
            return Response(LPOAttachmentSerializer(qs, many=True).data)

        # POST → upload
        f = request.FILES.get("file")
        kind = request.data.get("kind", "other")
        if not f:
            return Response({"detail": "No file"}, status=400)

        if not lpo.is_editable and not is_manager_or_owner(request.user):
            return Response({"detail": "Attachments locked after approval."}, status=403)

        allowed = set(getattr(settings, "ALLOWED_ATTACHMENT_CONTENT_TYPES", [])) or {
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/pdf",
        }
        ctype = (getattr(f, "content_type", "") or "").lower()
        if ctype not in allowed:
            return Response({"detail": "Unsupported file type."}, status=400)

        # IMAGES → compress to JPEG
        if ctype.startswith("image/"):
            try:
                img = Image.open(f)
                img.verify()
                f.seek(0)
                img = Image.open(f).convert("RGB")

                MAX_DIM = int(getattr(settings, "IMAGE_MAX_DIM", 2000))
                img.thumbnail((MAX_DIM, MAX_DIM))

                buf = BytesIO()
                img.save(buf, format="JPEG", quality=80, optimize=True)
                data = buf.getvalue()

                MAX_IMG_KB = int(getattr(settings, "MAX_IMAGE_UPLOAD_KB", 300))
                size_kb = round(len(data) / 1024, 1)
                if size_kb > MAX_IMG_KB:
                    return Response(
                        {"detail": f"Image too large after compression ({size_kb}KB > {MAX_IMG_KB}KB)."},
                        status=400,
                    )

                checksum = hashlib.md5(data).hexdigest()
                existing = LPOAttachment.objects.filter(lpo=lpo, checksum=checksum).first()
                if existing:
                    return Response(LPOAttachmentSerializer(existing).data, status=200)

                safe_name = (getattr(f, "name", "upload") or "upload").rsplit(".", 1)[0] + ".jpg"
                content = ContentFile(data, name=safe_name)
                att = LPOAttachment.objects.create(
                    lpo=lpo,
                    file=content,
                    kind=kind,
                    mime_type="image/jpeg",
                    size_kb=size_kb,
                    width=img.width,
                    height=img.height,
                    checksum=checksum,
                )
                return Response(LPOAttachmentSerializer(att).data, status=201)

            except UnidentifiedImageError:
                return Response({"detail": "Invalid image file."}, status=400)
            except Image.DecompressionBombError:
                return Response({"detail": "Image too large / unsafe to process."}, status=400)

        # PDF
        if ctype == "application/pdf":
            max_mb = int(getattr(settings, "MAX_PDF_UPLOAD_MB", 5))
            size = getattr(f, "size", None)
            if size and size > max_mb * 1024 * 1024:
                return Response({"detail": f"PDF too large (max {max_mb}MB)."}, status=400)
            raw = f.read()
            f.seek(0)
            checksum = hashlib.md5(raw).hexdigest()

            existing = LPOAttachment.objects.filter(lpo=lpo, checksum=checksum).first()
            if existing:
                return Response(LPOAttachmentSerializer(existing).data, status=200)

            att = LPOAttachment.objects.create(
                lpo=lpo,
                file=f,
                kind=kind,
                mime_type="application/pdf",
                size_kb=round((size or len(raw)) / 1024, 1),
                checksum=checksum,
            )
            return Response(LPOAttachmentSerializer(att).data, status=201)

        return Response({"detail": "Unsupported file type."}, status=400)


    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_pdf",
                   summary="Get LPO PDF",
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY, **COMMON_4XX})   # ← add
    @action(detail=True, methods=["get"], renderer_classes=[PDFRenderer], url_path="pdf")
    def pdf(self, request, pk=None):
        lpo = self.get_object()
        data = render_lpo_pdf_bytes(lpo)
        resp = Response(data, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{lpo.lpo_number}.pdf"'
        return resp

    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_delete_attachment",
                   summary="Delete LPO attachment",
                   responses={204: OpenApiResponse(description="No content"), **COMMON_4XX})  # ← add
    @action(detail=True, methods=["delete"], url_path=r"attachments/(?P<att_id>\d+)")
    def delete_attachment(self, request, pk=None, att_id=None):
        lpo = self.get_object()
        att = lpo.attachments.filter(id=att_id).first()
        if not att:
            return Response({"detail": "Not found."}, status=404)
        # lock after approval unless Manager/Owner
        if not getattr(lpo, "is_editable", True) and not is_manager_or_owner(request.user):
            return Response({"detail": "Attachments locked after approval."}, status=403)
        att.delete()
        return Response(status=204)

    def destroy(self, request, *args, **kwargs):
        lpo = self.get_object()
        if not LPOWritePolicy().has_object_permission(request, self, lpo):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You cannot delete this LPO.")

        lpo.deleted = True
        lpo.save(update_fields=["deleted"])

        AuditLog.objects.create(
            actor=request.user,
            verb="soft_delete",
            lpo=lpo,
            payload={"lpo_number": lpo.lpo_number},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_summary",
                   summary="LPO KPIs",
                   responses={200: OpenApiTypes.OBJECT, **COMMON_4XX})                      # ← add
    @action(detail=False, methods=["get"], url_path="summary", permission_classes=[permissions.IsAuthenticated])
    def summary(self, request):
        qs = self.get_queryset()  # already role-scoped
        counts = qs.values_list("status").order_by().annotate(c=models.Count("id"))
        return Response({"total": qs.count(), "by_status": dict(counts)})
    
# ---------- GRN ----------
@extend_schema_view(
    list=extend_schema(tags=["Procurement / GRN"], operation_id="grn_list", summary="List goods receipts",
                       responses={200: GoodsReceiptSerializer(many=True), **COMMON_4XX}),
    retrieve=extend_schema(tags=["Procurement / GRN"], operation_id="grn_retrieve", summary="Get goods receipt",
                           responses={200: GoodsReceiptSerializer, **COMMON_4XX}),
    create=extend_schema(tags=["Procurement / GRN"], operation_id="grn_create", summary="Create goods receipt",
                         responses={201: GoodsReceiptSerializer, **COMMON_4XX}),
    update=extend_schema(tags=["Procurement / GRN"], operation_id="grn_update", summary="Replace goods receipt",
                         responses={200: GoodsReceiptSerializer, **COMMON_4XX}),
    partial_update=extend_schema(tags=["Procurement / GRN"], operation_id="grn_partial_update", summary="Update goods receipt",
                                 responses={200: GoodsReceiptSerializer, **COMMON_4XX}),
    destroy=extend_schema(tags=["Procurement / GRN"], operation_id="grn_destroy", summary="Delete goods receipt",
                          responses={204: OpenApiResponse(description="No content"), **COMMON_4XX}),
)
class GoodsReceiptViewSet(viewsets.ModelViewSet):
    queryset = GoodsReceipt.objects.select_related("lpo", "lpo__supplier")
    serializer_class = GoodsReceiptSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        # Validation (including LPO status gate) happens in the serializer.
        obj = serializer.save()

        AuditLog.objects.create(
            actor=self.request.user,
            verb="create",
            grn=obj,
            payload={"lpo": getattr(obj.lpo, "lpo_number", None)},
        )

    def get_queryset(self):
        return super().get_queryset()
    