# inventory/views.py
from __future__ import annotations
import io, csv
import pandas as pd
from decimal import Decimal
from django.http import StreamingHttpResponse
from django.db import transaction, models
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework.exceptions import PermissionDenied

from accounts.permissions import IsSuperAdmin

from .models import InventoryEntry, AuditLog
from .permissions import ReadOnlyOrSuperAdmin
from .filters import apply_inventory_filters

from .serializers import InventoryAttachmentSerializer, InventoryEntrySerializer, AuditLogSerializer
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
import datetime, uuid

from io import BytesIO
import hashlib
from PIL import Image, UnidentifiedImageError, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

from django.conf import settings
from django.core.files.base import ContentFile
from .models import InventoryEntry, InventoryAttachment

def _json_safe(v):
    if isinstance(v, (datetime.date, datetime.datetime, uuid.UUID)):
        return v.isoformat() if hasattr(v, "isoformat") else str(v)
    if isinstance(v, Decimal):
        # choose str to preserve precision
        return str(v)
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v

LIST_PARAMS = [
    OpenApiParameter(name="q", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, description="Free text search"),
    OpenApiParameter(name="status", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, description="Status filter"),
    OpenApiParameter(name="from", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, description="Start date (inclusive)"),
    OpenApiParameter(name="to", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, description="End date (inclusive)"),
    OpenApiParameter(name="limit", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY),
    OpenApiParameter(name="offset", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY),
]

class InventoryViewSet(viewsets.ModelViewSet):
    queryset = InventoryEntry.objects.filter(deleted=False)
    serializer_class = InventoryEntrySerializer
    permission_classes = [ ReadOnlyOrSuperAdmin]  
    parser_classes = (JSONParser, MultiPartParser, FormParser)

    def get_permissions(self):
        if getattr(self, "action", None) in {"import_excel", "audit_logs"}:
            return [IsSuperAdmin()]
        return [perm() for perm in self.permission_classes]
    
    def get_queryset(self):
        qs = super().get_queryset()
        # spectacular may call without a real request
        qparams = getattr(self.request, "query_params", {}) or {}
        return apply_inventory_filters(qs, qparams)

    # ---- audit logging centralized here ----
    def perform_create(self, serializer):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Only superadmin can create inventory.")
        obj = serializer.save(created_by=self.request.user)
        AuditLog.objects.create(entry=obj, user=self.request.user,
                                action="create", changes=_json_safe(serializer.validated_data))

    def perform_update(self, serializer):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Only superadmin can update inventory.")
        instance = self.get_object()
        before = {f: getattr(instance, f) for f in serializer.validated_data.keys()}
        obj = serializer.save(modified_by=self.request.user)
        delta = {k: {"from": before.get(k), "to": v} for k, v in serializer.validated_data.items() if before.get(k) != v}
        if delta:
            AuditLog.objects.create(entry=obj, user=self.request.user,
                                    action="update", changes=_json_safe(delta))

        if not self.request.user.is_superuser:
            log.warning("BLOCKED %s by user=%s (superuser=%s, path=%s)",
                        self.action,
                        getattr(self.request.user, "username", None),
                        getattr(self.request.user, "is_superuser", None),
                        self.request.path)
            raise PermissionDenied("Only superadmin can update inventory.")
        
    def perform_destroy(self, instance):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Only superadmin can delete inventory.")
        instance.soft_delete()
        AuditLog.objects.create(entry=instance, user=self.request.user, action="soft_delete")

    def create(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superadmin can create inventory.")
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superadmin can update inventory.")
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superadmin can update inventory.")
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superadmin can delete inventory.")
        return super().destroy(request, *args, **kwargs)
    
    # ---- list (OpenAPI) ----
    @extend_schema(
        parameters=LIST_PARAMS,
        responses={200: InventoryEntrySerializer(many=True)},
        tags=["Inventory"],
        operation_id="inventory_list",
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    # ---- summary ----
    @extend_schema(
        parameters=LIST_PARAMS[:4],  # q, status, from, to
        responses={200: OpenApiTypes.OBJECT},
        tags=["Inventory"],
        operation_id="inventory_summary",
    )
    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        total = qs.count()
        by_status = {k: qs.filter(status=k).count() for k, _ in InventoryEntry.STATUS_CHOICES}
        totals = qs.aggregate(
            total_quantity=models.Sum("quantity"),
            total_net_weight=models.Sum("net_weight"),
        )
        return Response({"total": total, "by_status": by_status, **totals})

    # ---- import (.xlsx/.csv) ----
    @action(
        detail=False,
        methods=["post"],
        url_path="import-excel",
        parser_classes=[MultiPartParser, FormParser],
        permission_classes=[IsSuperAdmin],
    )
    @extend_schema(
        description=(
            "Upload .xlsx or .csv via multipart/form-data with field `file`.\n"
            "Required columns: date, truck_registration, quantity (aliases supported)."
        ),
        request={"multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}},
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiResponse(description="Invalid file or content")},
        tags=["Inventory"],
        operation_id="inventory_import_excel",
    )
    def import_excel(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superadmin can import inventory.")

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"detail": "file required"}, status=status.HTTP_400_BAD_REQUEST)

        # Read once
        try:
            name = (getattr(file_obj, "name", "") or "").lower()
            content = file_obj.read()
            file_obj.seek(0)

            if name.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(content))
            elif name.endswith((".xls", ".xlsx")):
                df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
            else:
                # Unknown or missing extension: try Excel first, then CSV
                try:
                    df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
                except Exception:
                    df = pd.read_csv(io.BytesIO(content))
        except Exception as e:
            # Tests expect this shape/message on bad files
            return Response({"detail": f"error reading file: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        # normalize headers (keep the rest of your function unchanged)
        def _norm(s: str) -> str:
            return str(s).strip().lower().replace(" ", "_")
        
        cols = {_norm(c) for c in df.columns}
        aliases = {  # keep exactly in sync with the one you use later
            "customer_name": ["customer"],
            "mineral_or_equipment": ["mineral", "equipment", "mineral/equipment"],
            "supplier_agent": ["supplier/agent", "agent", "supplier"],
            "truck_registration": ["truck", "truck_no", "truck_number", "truck_reg"],
            "status": [],
            "driver_name": ["driver"],
            "driver_phone": ["phone", "driver_phone_no", "driver_gsm"],
            "quantity": ["tonnage", "tonnage/tons", "tons", "qty"],
            "origin": ["loading_site", "loading", "site"],
            "destination": ["dest"],
            "location": ["yard", "station"],
            "transporter_name": ["transporter", "transporter_name_"],
            "description": ["desc", "details"],
            "payment_type": ["payment", "payment_method"],
            "analysis_results": ["analysis", "assay"],
            "gross_weight": ["gross", "gross_kg", "gross_weight_kg"],
            "tare_weight": ["tare", "tare_kg", "tare_weight_kg"],
            "net_weight": ["net", "net_kg", "net_weight_kg"],
            "comment": ["comments", "remark", "remarks"],
        }
        
        def _present(key: str) -> bool:
            return (key in cols) or any(a in cols for a in aliases.get(key, []))
        
        if not (_present("date") or _present("truck_registration")):
            # This matches the test's expectation text
            return Response({"detail": "error reading file: required headers not found"}, status=status.HTTP_400_BAD_REQUEST)

        def pick(row, key):
            if key in row and pd.notna(row[key]):
                return row[key]
            for alt in aliases.get(key, []):
                if alt in row and pd.notna(row[alt]):
                    return row[alt]
            return None

        def to_decimal(val):
            if val is None or (isinstance(val, float) and pd.isna(val)) or (isinstance(val, str) and val.strip() == ""):
                return None
            try:
                return Decimal(str(val))
            except Exception:
                return None

        required_heads = ["date", "truck_registration"]
        missing = [h for h in required_heads if h not in df.columns and not any(a in df.columns for a in aliases.get(h, []))]

        created, errors = 0, []
        with transaction.atomic():
            for idx, row in df.iterrows():
                row = row.to_dict()

                raw_date = pick(row, "date") if "date" in row else row.get("date")
                try:
                    date_val = pd.to_datetime(raw_date, errors="coerce").date() if raw_date is not None else None
                except Exception:
                    date_val = None

                truck = pick(row, "truck_registration")
                qty   = pick(row, "quantity")

                if date_val is None or not truck or qty is None:
                    errors.append({"row": int(idx) + 1, "errors": {"detail": "missing required: date/truck_registration/quantity"}})
                    continue

                payload = {
                    "date": date_val,
                    "customer_name": pick(row, "customer_name"),
                    "mineral_or_equipment": pick(row, "mineral_or_equipment"),
                    "description": pick(row, "description"),
                    "supplier_agent": pick(row, "supplier_agent"),
                    "truck_registration": str(truck).strip().upper(),
                    "status": (str(pick(row, "status") or "pending").lower()),
                    "driver_name": pick(row, "driver_name"),
                    "driver_phone": str(pick(row, "driver_phone") or "").strip(),
                    "quantity": to_decimal(qty),
                    "unit": "tons",
                    "origin": pick(row, "origin"),
                    "destination": pick(row, "destination"),
                    "location": pick(row, "location"),
                    "transporter_name": pick(row, "transporter_name"),
                    "payment_type": (str(pick(row, "payment_type") or "") or None),
                    "analysis_results": pick(row, "analysis_results"),
                    "gross_weight": to_decimal(pick(row, "gross_weight")),
                    "tare_weight": to_decimal(pick(row, "tare_weight")),
                    "net_weight": to_decimal(pick(row, "net_weight")),
                    "comment": pick(row, "comment"),
                }

                ser = InventoryEntrySerializer(data=payload, context={"request": request})
                if ser.is_valid():
                    obj = ser.save(created_by=request.user)
                    AuditLog.objects.create(entry=obj, user=request.user,
                        action="create", changes=_json_safe(payload))
                    created += 1
                else:
                    errors.append({"row": int(idx) + 1, "errors": ser.errors})

        return Response({"created": created, "errors": errors, "missing_columns": missing})

    # ---- export (CSV) ----
    @extend_schema(
        parameters=LIST_PARAMS[:4],  # q, status, from, to
        responses={200: OpenApiResponse(description="CSV file", response=OpenApiTypes.BINARY)},
        tags=["Reports"],
        operation_id="inventory_export_csv",
        description="Download filtered inventory as CSV. Honors q, status, from, to.",
    )
    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        qs = self.filter_queryset(self.get_queryset())
        fields = [
            "date","customer_name","mineral_or_equipment","description","supplier_agent",
            "truck_registration","status","driver_name","driver_phone","quantity","unit",
            "origin","destination","location","transporter_name","payment_type",
            "analysis_results","gross_weight","tare_weight","net_weight","comment",
            "created_at","updated_at",
        ]

        def rowgen():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(fields)
            yield buffer.getvalue()
            buffer.seek(0); buffer.truncate(0)
            for obj in qs.iterator():
                writer.writerow([getattr(obj, f) if getattr(obj, f) is not None else "" for f in fields])
                yield buffer.getvalue()
                buffer.seek(0); buffer.truncate(0)

        resp = StreamingHttpResponse(rowgen(), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename=\"inventory_export.csv\"'
        return resp

    # --- NEW: nested audit logs for a specific entry ---
    @action(
        detail=True,
        methods=["get"],
        url_path="audit-logs",
        permission_classes=[IsSuperAdmin],
    )
    @extend_schema(
        responses={200: AuditLogSerializer(many=True)},
        operation_id="inventory_audit_logs",
        description="List audit events for a specific inventory entry (most recent first).",
    )
    def audit_logs(self, request, pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superadmin can view audit logs.")

        entry = self.get_object()

        logs = entry.audit_logs.select_related("user").all().order_by("-timestamp")
        page = self.paginate_queryset(logs)
        if page is not None:
            ser = AuditLogSerializer(page, many=True)
            return self.get_paginated_response(ser.data)
        ser = AuditLogSerializer(logs, many=True)
        return Response(ser.data)


# --- NEW: Standalone read-only viewset (optional but handy for admin tools) ---
@extend_schema(
    parameters=[
        OpenApiParameter(name="entry", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, description="Filter by InventoryEntry ID (UUID)"),
        OpenApiParameter(name="user", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, description="Filter by username"),
        OpenApiParameter(name="action", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, description="Filter by action (create/update/soft_delete)"),
    ],
    tags=["Inventory"],
)
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Superadmin-only read-only listing of audit events."""
    queryset = AuditLog.objects.select_related("entry", "user").all().order_by("-timestamp")
    serializer_class = AuditLogSerializer
    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        qs = super().get_queryset()
        entry_id = self.request.query_params.get("entry")
        username = self.request.query_params.get("user")
        action = self.request.query_params.get("action")

        if entry_id:
            qs = qs.filter(entry_id=entry_id)
        if username:
            qs = qs.filter(user__username=username)
        if action:
            qs = qs.filter(action=action)

        return qs
    
class InventoryEntryViewSet(viewsets.ModelViewSet):
    queryset = InventoryEntry.objects.all()
    serializer_class = InventoryEntrySerializer       
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(                                       
        methods=['get'],
        tags=["Inventory / Attachments"],
        operation_id="inventory_entry_attachments_list",
        responses=InventoryAttachmentSerializer(many=True),
    )
    @extend_schema(                                     
        methods=['post'],
        tags=["Inventory / Attachments"],
        operation_id="inventory_entry_attachments_create",
        request={"multipart/form-data": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "kind": {"type": "string", "enum": ["photo", "spec", "other"]},
            },
            "required": ["file"],
        }},
        responses={201: InventoryAttachmentSerializer},
    )
    @action(detail=True, methods=["get", "post"], url_path="attachments")
    def attachments(self, request, pk=None):

        entry = self.get_object()

        # GET → list
        if request.method == "GET":
            qs = entry.attachments.all().order_by("-uploaded_at")
            return Response(InventoryAttachmentSerializer(qs, many=True).data)

        # POST → upload (images strongly preferred)
        f = request.FILES.get("file")
        kind = request.data.get("kind", "photo")
        if not f:
            return Response({"detail": "No file"}, status=400)

        allowed = set(getattr(settings, "ALLOWED_ATTACHMENT_CONTENT_TYPES", [])) or {
            "image/jpeg", "image/png", "image/webp", "application/pdf"
        }
        ctype = (getattr(f, "content_type", "") or "").lower()
        if ctype not in allowed:
            return Response({"detail": "Unsupported file type."}, status=400)

        # IMAGES → canonicalize to JPEG
        if ctype.startswith("image/"):
            try:
                img = Image.open(f); img.verify(); f.seek(0)
                img = Image.open(f).convert("RGB")

                MAX_DIM = int(getattr(settings, "IMAGE_MAX_DIM", 2000))
                img.thumbnail((MAX_DIM, MAX_DIM))

                buf = BytesIO()
                img.save(buf, format="JPEG", quality=80, optimize=True)
                data = buf.getvalue()

                # Optional hard cap
                MAX_IMG_KB = int(getattr(settings, "MAX_IMAGE_UPLOAD_KB", 300))
                size_kb = round(len(data) / 1024, 1)
                if size_kb > MAX_IMG_KB:
                    return Response({"detail": f"Image too large after compression ({size_kb}KB > {MAX_IMG_KB}KB)."}, status=400)

                checksum = hashlib.md5(data).hexdigest()
                existing = InventoryAttachment.objects.filter(entry=entry, checksum=checksum).first()
                if existing:
                    return Response(InventoryAttachmentSerializer(existing).data, status=200)

                safe_name = (getattr(f, "name", "upload") or "upload").rsplit(".", 1)[0] + ".jpg"
                content = ContentFile(data, name=safe_name)

                att = InventoryAttachment.objects.create(
                    entry=entry, file=content, kind=kind, mime_type="image/jpeg",
                    size_kb=size_kb, width=img.width, height=img.height,
                    checksum=checksum, uploaded_by=request.user,
                )
                return Response(InventoryAttachmentSerializer(att).data, status=201)

            except UnidentifiedImageError:
                return Response({"detail": "Invalid image file."}, status=400)
            except Image.DecompressionBombError:
                return Response({"detail": "Image too large / unsafe to process."}, status=400)

        # PDF (e.g., spec sheets)
        if ctype == "application/pdf":
            max_mb = int(getattr(settings, "MAX_PDF_UPLOAD_MB", 5))
            size = getattr(f, "size", None)
            if size and size > max_mb * 1024 * 1024:
                return Response({"detail": f"PDF too large (max {max_mb}MB)."}, status=400)
            raw = f.read(); f.seek(0)
            checksum = hashlib.md5(raw).hexdigest()
            existing = InventoryAttachment.objects.filter(entry=entry, checksum=checksum).first()
            if existing:
                return Response(InventoryAttachmentSerializer(existing).data, status=200)

            att = InventoryAttachment.objects.create(
                entry=entry, file=f, kind=kind, mime_type="application/pdf",
                size_kb=round((size or len(raw))/1024, 1),
                checksum=checksum, uploaded_by=request.user,
            )
            return Response(InventoryAttachmentSerializer(att).data, status=201)

        return Response({"detail": "Unsupported file type."}, status=400)

    @extend_schema(tags=["Inventory / Attachments"], operation_id="inventory_entry_delete_attachment")
    @action(detail=True, methods=["delete"], url_path=r"attachments/(?P<att_id>\d+)")
    def delete_attachment(self, request, pk=None, att_id=None):
        entry = self.get_object()
        att = entry.attachments.filter(id=att_id).first()
        if not att:
            return Response({"detail": "Not found."}, status=404)
        # Add your permission rule if needed (e.g., only managers delete)
        att.delete()
        return Response(status=204)