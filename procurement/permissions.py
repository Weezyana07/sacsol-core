# procurement/permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS
from core.roles import in_groups, is_owner, is_manager_or_owner

class LPOPolicy(BasePermission):
    """
    - LIST (SAFE, action=list): Manager/Owner only
    - RETRIEVE (SAFE, action=retrieve): Manager/Owner or the creator (staff)
    - CREATE (POST): Staff/Manager/Owner
    - UPDATE/DELETE: Owner only (or keep your _can_edit rules in viewset)
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            # list vs retrieve differs: we check view.action
            if getattr(view, "action", None) == "list":
                return is_manager_or_owner(request.user)
            # allow retrieve to pass into object-level check
            return bool(request.user and request.user.is_authenticated)

        if request.method == "POST":
            # anyone in the org roles can create
            from core.roles import is_staff_or_manager_or_owner
            return is_staff_or_manager_or_owner(request.user)

        # edits default to owner-only (keeps your stricter rule)
        return is_owner(request.user)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            # managers/owner see any; staff can see only what they created
            return is_manager_or_owner(request.user) or (obj.created_by_id == request.user.id)
        # non-safe: defer to has_permission outcome
        return self.has_permission(request, view)


class LPOReadPolicy(BasePermission):
    """
    - Owner/Manager: can read any LPO
    - Staff: can read only LPOs they created
    """
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            if is_owner(request.user) or in_groups(request.user, "Manager"):
                return True
            return obj.created_by_id == request.user.id
        return True  # non-safe handled elsewhere

class LPOWritePolicy(BasePermission):
    """
    - Owner/Manager: can update/approve
    - Staff: can update/submit only their own LPO while in 'draft'
    """
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if is_owner(request.user) or in_groups(request.user, "Manager"):
            return True
        # staff: only their own draft on non-safe methods
        return obj.created_by_id == request.user.id and getattr(obj, "status", "") == "draft"
    