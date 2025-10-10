# inventory/filters.py
from datetime import date
from django.db.models import Q

SEARCH_FIELDS = [
    "customer_name", "mineral_or_equipment", "truck_registration",
    "origin", "destination", "location", "driver_name", "supplier_agent",
    "transporter_name", "analysis_results", "comment",
]

def apply_inventory_search(queryset, q: str):
    if not q:
        return queryset
    q = q.strip()
    cond = Q()
    for f in SEARCH_FIELDS:
        cond |= Q(**{f"{f}__icontains": q})
    return queryset.filter(cond)

def apply_inventory_filters(qs, params):
    """
    Supports FE params: q, status, from, to (inclusive range on `date`).
    """
    q = params.get("q") or params.get("search")
    status_param = params.get("status")
    from_str = params.get("from")
    to_str = params.get("to")

    if q:
        qs = apply_inventory_search(qs, q)

    if status_param:
        qs = qs.filter(status=status_param)

    if from_str:
        qs = qs.filter(date__gte=from_str)
    if to_str:
        qs = qs.filter(date__lte=to_str)

    return qs