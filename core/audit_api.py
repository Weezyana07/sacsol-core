# core/audit_api.py
from __future__ import annotations
from typing import Any, Dict, List
from django.db.models import Q
from rest_framework import permissions, serializers
from rest_framework.generics import ListAPIView
from rest_framework.pagination import LimitOffsetPagination
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes, OpenApiResponse

from inventory.models import AuditLog as InvAuditLog
from procurement.models import AuditLog as LPOAuditLog, LPO


class AuditRowOut(serializers.Serializer):
    id = serializers.CharField()
    source = serializers.ChoiceField(choices=["inventory", "lpo"])
    entry_id = serializers.CharField()
    entry_label = serializers.CharField(allow_null=True, required=False)
    user_username = serializers.CharField()
    action = serializers.CharField()
    changes = serializers.JSONField(required=False)
    timestamp = serializers.DateTimeField(allow_null=True)  # ← allow None defensively


class Pager(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


def _best_ts(o):
    for f in ("created_at", "timestamp", "created", "time", "ts"):
        if hasattr(o, f):
            return getattr(o, f)
    return None

def _best_username(o):
    if hasattr(o, "actor") and getattr(o.actor, "username", None):
        return o.actor.username
    if hasattr(o, "user") and getattr(o.user, "username", None):
        return o.user.username
    for f in ("user_username", "actor_username", "username"):
        if hasattr(o, f) and getattr(o, f):
            return getattr(o, f)
    return ""

def _best_action(obj):
    for f in ("verb", "action", "event"):
        if hasattr(obj, f) and getattr(obj, f):
            return getattr(obj, f)
    return ""


def _inv_map(r) -> Dict[str, Any]:
    return {
        "id": f"inv-{r.id}",
        "source": "inventory",
        "entry_id": str(getattr(r, "entry_id", r.id)),
        "entry_label": getattr(r, "entry_label", None),
        "user_username": _best_username(r),
        "action": _best_action(r),
        "changes": getattr(r, "payload", getattr(r, "changes", {})) or {},
        "timestamp": _best_ts(r),
    }


def _lpo_map(r):
    lpo = getattr(r, "lpo", None)
    return {
        "id": f"lpo-{r.id}",
        "source": "lpo",
        "entry_id": str(getattr(lpo, "id", getattr(r, "lpo_id", ""))),
        "entry_label": getattr(lpo, "lpo_number", None),
        "user_username": _best_username(r),
        "action": getattr(r, "verb", getattr(r, "action", getattr(r, "event", ""))),
        "changes": getattr(r, "payload", {}) or {},
        "timestamp": _best_ts(r),
    }

@extend_schema(
    tags=["Core / Audit"],
    summary="Unified audit feed (Inventory + LPO)",
    parameters=[
        OpenApiParameter(name="user", location=OpenApiParameter.QUERY, required=False, type=OpenApiTypes.STR, description="Username icontains", ),
        OpenApiParameter(name="action", location=OpenApiParameter.QUERY, required=False, type=OpenApiTypes.STR, description="create/update/submitted/approved/cancelled/soft_delete",),
        OpenApiParameter( name="entry", location=OpenApiParameter.QUERY, required=False, type=OpenApiTypes.STR, description="UUID / entry code / LPO number",),
        OpenApiParameter(name="source", location=OpenApiParameter.QUERY, required=False, type=OpenApiTypes.STR, enum=["inventory", "lpo"], description="Limit by source",),
        OpenApiParameter(name="limit", location=OpenApiParameter.QUERY, required=False, type=OpenApiTypes.INT,),
        OpenApiParameter(name="offset", location=OpenApiParameter.QUERY, required=False, type=OpenApiTypes.INT,),
    ],
    responses={
        200: AuditRowOut(many=True),
        401: OpenApiResponse(description="Authentication credentials were not provided or are invalid."),
        403: OpenApiResponse(description="You do not have permission to perform this action."),
    },
)
class AuditLogsList(ListAPIView):
    ...
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AuditRowOut
    pagination_class = Pager

    def list(self, request, *args, **kwargs):
        user_q   = (request.query_params.get("user")   or "").strip()
        action_q = (request.query_params.get("action") or "").strip()
        entry_q  = (request.query_params.get("entry")  or "").strip()
        src_q    = (request.query_params.get("source") or request.query_params.get("src") or "").strip()

        rows: List[Dict[str, Any]] = []

        # INVENTORY
        if src_q in ("", "inventory"):
            inv = InvAuditLog.objects.select_related("user")  # ← was actor
            if user_q:
                inv = inv.filter(Q(user__username__icontains=user_q) | Q(user_username__icontains=user_q))
            if action_q:
                inv = inv.filter(action__iexact=action_q)  # ← inventory uses `action`
            if entry_q:
                inv = inv.filter(Q(entry_id__icontains=entry_q) | Q(entry_label__icontains=entry_q))
            inv = inv.order_by("-created_at") if hasattr(InvAuditLog, "created_at") else inv.order_by("-id")
            rows.extend(_inv_map(r) for r in inv[:1000])

        # LPO (procurement)
        if src_q in ("", "lpo"):
            lpo = LPOAuditLog.objects.select_related("lpo", "actor")
            if user_q:
                lpo = lpo.filter(
                    Q(actor__username__icontains=user_q)
                    | Q(user__username__icontains=user_q)
                    | Q(user_username__icontains=user_q)
                    | Q(actor_username__icontains=user_q)
                )
            if action_q:
                lpo = lpo.filter(verb__iexact=action_q)  # ← LPO uses `verb` only
            if entry_q:
                lpo = lpo.filter(
                    Q(lpo__lpo_number__icontains=entry_q)
                    | Q(lpo_id__in=LPO.objects.filter(lpo_number__icontains=entry_q).values("id"))
                )
            lpo = lpo.order_by("-created_at") if hasattr(LPOAuditLog, "created_at") else lpo.order_by("-id")
            rows.extend(_lpo_map(r) for r in lpo[:1000])


        # unify ordering + paginate
        rows.sort(key=lambda r: (r.get("timestamp") or ""), reverse=True)
        page = self.paginate_queryset(rows)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)