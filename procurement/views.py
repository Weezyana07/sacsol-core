# procurement/views.py
from __future__ import annotations

from django.conf import settings
from django.core.files.base import ContentFile
from django.http import HttpResponse

from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiTypes

from io import BytesIO
import hashlib

from PIL import Image, UnidentifiedImageError, ImageFile  # requires Pillow
ImageFile.LOAD_TRUNCATED_IMAGES = True  # tolerate truncated streams safely

from inventory.permissions import in_group  # your helper: checks group by name

from .models import Supplier, LPO, LPOAttachment, GoodsReceipt, AuditLog
from .serializers import (
    SupplierSerializer, LPOSerializer, LPOAttachmentSerializer, GoodsReceiptSerializer
)
from .services import render_lpo_pdf_bytes
from rest_framework.exceptions import ValidationError


def _ensure_lpo_can_receive(lpo):
    allowed = {
        getattr(LPO, "STATUS_APPROVED", "approved"),
        getattr(LPO, "STATUS_PARTIAL", "partially_received"),
    }
    if lpo.status not in allowed:
        raise ValidationError("Only approved or partially received LPO can receive goods.")

# ---------- Supplier ----------
@extend_schema_view(
    list=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_list"),
    retrieve=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_retrieve"),
    create=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_create"),
    update=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_update"),
    partial_update=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_partial_update"),
    destroy=extend_schema(tags=["Procurement / Suppliers"], operation_id="supplier_destroy"),
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
    list=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_list"),
    retrieve=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_retrieve"),
    create=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_create"),
    update=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_update"),
    partial_update=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_partial_update"),
    destroy=extend_schema(tags=["Procurement / LPO"], operation_id="lpo_destroy"),
)
class LPOViewSet(viewsets.ModelViewSet):
    queryset = (
        LPO.objects.select_related("supplier", "approved_by", "created_by")
        .prefetch_related("items", "attachments")
        .all()
    )
    serializer_class = LPOSerializer
    permission_classes = [permissions.IsAuthenticated]

    # ---- helpers (permissions) ----
    @staticmethod
    def _is_manager(user) -> bool:
        return bool(user.is_superuser or in_group(user, "manager"))

    def _can_edit(self, request, lpo: LPO) -> bool:
        """Staff: edit own LPO while editable; Manager/Superuser: always."""
        if self._is_manager(request.user):
            return True
        return lpo.is_editable and lpo.created_by_id == request.user.id

    @staticmethod
    def _forbidden(msg="You can only edit your own draft/submitted LPO."):
        return Response({"detail": msg}, status=status.HTTP_403_FORBIDDEN)

    # ---- queryset + filters ----
    def get_queryset(self):
        qs = super().get_queryset().filter(deleted=False)
        status_q = self.request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        supplier = self.request.query_params.get("supplier")
        if supplier:
            qs = qs.filter(supplier_id=supplier)
        return qs

    # ---- CRUD overrides to enforce edit-own-until-submitted + soft delete ----
    def update(self, request, *args, **kwargs):
        lpo = self.get_object()
        if not self._can_edit(request, lpo):
            return self._forbidden()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        lpo = self.get_object()
        if not self._can_edit(request, lpo):
            return self._forbidden()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        lpo = self.get_object()
        if not self._can_edit(request, lpo):
            return self._forbidden()
        # soft-delete
        lpo.deleted = True
        lpo.save(update_fields=["deleted"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---- actions ----
    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_submit", responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        lpo = self.get_object()

        # Submit allowed by creator, manager or superuser
        if not (self._is_manager(request.user) or lpo.created_by_id == request.user.id):
            return self._forbidden("Only creator or Manager/Superuser can submit this LPO.")

        try:
            lpo.submit(request.user)
            lpo.save(update_fields=["status", "submitted_at"])
            AuditLog.objects.create(actor=request.user, verb="submitted", lpo=lpo)
            return Response({"status": lpo.status})
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_approve", responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        # Enforce Manager or Superuser
        if not self._is_manager(request.user):
            return Response({"detail": "Only Manager or Superuser can approve."}, status=403)

        lpo = self.get_object()
        try:
            lpo.approve(request.user)
            lpo.save(update_fields=["status", "approved_at", "approved_by"])
            AuditLog.objects.create(actor=request.user, verb="approved", lpo=lpo)
            # TODO: trigger email + PDF generation here if desired
            return Response({"status": lpo.status})
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_cancel", responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        # Let Manager or Superuser cancel
        if not self._is_manager(request.user):
            return Response({"detail": "Only Manager or Superuser can cancel."}, status=403)

        lpo = self.get_object()
        try:
            lpo.cancel(request.user)
            lpo.save(update_fields=["status"])
            AuditLog.objects.create(actor=request.user, verb="cancelled", lpo=lpo)
            return Response({"status": lpo.status})
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # ---------- Attachments (GET list + POST upload combined) ----------
    @extend_schema(  # GET schema
        methods=["get"],
        tags=["Procurement / LPO Attachments"],
        operation_id="lpo_attachments_list",
        responses=LPOAttachmentSerializer(many=True),
    )
    @extend_schema(  # POST schema
        methods=["post"],
        tags=["Procurement / LPO Attachments"],
        operation_id="lpo_attachments_create",
        request={"multipart/form-data": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "kind": {"type": "string", "enum": ["quote", "invoice", "other"]},
            },
            "required": ["file"],
        }},
        responses={201: LPOAttachmentSerializer},
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
    
        if not lpo.is_editable and not self._is_manager(request.user):
            return Response({"detail": "Attachments locked after approval."}, status=403)
    
        allowed = set(getattr(settings, "ALLOWED_ATTACHMENT_CONTENT_TYPES", [])) or {
            "image/jpeg", "image/png", "image/webp", "application/pdf"
        }
        ctype = (getattr(f, "content_type", "") or "").lower()
        if ctype not in allowed:
            return Response({"detail": "Unsupported file type."}, status=400)
    
        # IMAGES
        if ctype.startswith("image/"):
            try:
                img = Image.open(f); img.verify(); f.seek(0)
                img = Image.open(f).convert("RGB")
                MAX_DIM = int(getattr(settings, "IMAGE_MAX_DIM", 2000))
                img.thumbnail((MAX_DIM, MAX_DIM))
                buf = BytesIO(); img.save(buf, format="JPEG", quality=80, optimize=True)
                data = buf.getvalue()
    
                MAX_IMG_KB = int(getattr(settings, "MAX_IMAGE_UPLOAD_KB", 300))
                size_kb = round(len(data) / 1024, 1)
                if size_kb > MAX_IMG_KB:
                    return Response({"detail": f"Image too large after compression ({size_kb}KB > {MAX_IMG_KB}KB)."}, status=400)
    
                checksum = hashlib.md5(data).hexdigest()
                existing = LPOAttachment.objects.filter(lpo=lpo, checksum=checksum).first()
                if existing:
                    return Response(LPOAttachmentSerializer(existing).data, status=200)
    
                safe_name = (getattr(f, "name", "upload") or "upload").rsplit(".", 1)[0] + ".jpg"
                content = ContentFile(data, name=safe_name)
                att = LPOAttachment.objects.create(
                    lpo=lpo, file=content, kind=kind, mime_type="image/jpeg",
                    size_kb=size_kb, width=img.width, height=img.height, checksum=checksum
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
            raw = f.read(); f.seek(0)
            checksum = hashlib.md5(raw).hexdigest()
    
            existing = LPOAttachment.objects.filter(lpo=lpo, checksum=checksum).first()
            if existing:
                return Response(LPOAttachmentSerializer(existing).data, status=200)
    
            att = LPOAttachment.objects.create(
                lpo=lpo, file=f, kind=kind, mime_type="application/pdf",
                size_kb=round((size or len(raw))/1024, 1), checksum=checksum
            )
            return Response(LPOAttachmentSerializer(att).data, status=201)
    
        return Response({"detail": "Unsupported file type."}, status=400)
    
    @extend_schema(
        tags=["Procurement / LPO"],
        operation_id="lpo_pdf",
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        lpo = self.get_object()
        data = render_lpo_pdf_bytes(lpo)
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{lpo.lpo_number}.pdf"'
        return resp

    @extend_schema(tags=["Procurement / LPO"], operation_id="lpo_delete_attachment")
    @action(detail=True, methods=["delete"], url_path=r"attachments/(?P<att_id>\d+)")
    def delete_attachment(self, request, pk=None, att_id=None):
        lpo = self.get_object()
        att = lpo.attachments.filter(id=att_id).first()
        if not att:
            return Response({"detail": "Not found."}, status=404)
        if not lpo.is_editable and not self._is_manager(request.user):
            return Response({"detail": "Attachments locked after approval."}, status=403)
        att.delete()
        return Response(status=204)


# ---------- GRN ----------
@extend_schema_view(
    list=extend_schema(tags=["Procurement / GRN"], operation_id="grn_list"),
    retrieve=extend_schema(tags=["Procurement / GRN"], operation_id="grn_retrieve"),
    create=extend_schema(tags=["Procurement / GRN"], operation_id="grn_create"),
)
class GoodsReceiptViewSet(viewsets.ModelViewSet):
    queryset = GoodsReceipt.objects.select_related("lpo", "lpo__supplier")
    serializer_class = GoodsReceiptSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        # Validation (including LPO status gate) is handled in the serializer.
        # Do not pass unknown kwargs like created_by.
        serializer.save()

    def get_queryset(self):
        # Single-tenant → no org filter needed
        return super().get_queryset()