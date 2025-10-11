# accounts/signals.py
from django.contrib.auth.models import Group

def ensure_groups(sender, **kwargs):
    """
    Ensure canonical groups exist and merge/delete lowercase duplicates.
    """
    canonical = ["Staff", "Manager"]
    for name in canonical:
        Group.objects.get_or_create(name=name)

    # auto-merge any accidental lowercase groups into canonical ones
    for src, dst in (("manager", "Manager"), ("staff", "Staff")):
        try:
            lower = Group.objects.get(name=src)
            upper, _ = Group.objects.get_or_create(name=dst)
            for u in lower.user_set.all():
                u.groups.add(upper)
                u.groups.remove(lower)
            lower.delete()
        except Group.DoesNotExist:
            continue