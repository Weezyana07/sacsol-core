# core/roles.py
from django.contrib.auth.models import Group

def is_owner(user) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)

def in_groups(user, *names: str) -> bool:
    return bool(
        user and user.is_authenticated
        and user.groups.filter(name__iregex=r"^(" + "|".join(names) + r")$").exists()
    )

def is_manager_or_owner(user) -> bool:
    return is_owner(user) or in_groups(user, "Manager")

def is_staff_or_manager_or_owner(user) -> bool:
    return is_owner(user) or in_groups(user, "Manager", "Staff")