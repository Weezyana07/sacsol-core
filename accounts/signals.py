# accounts/signals.py
from django.contrib.auth.models import Group

def ensure_groups(sender, **kwargs):
    for name in ["Staff", "Manager"]:
        Group.objects.get_or_create(name=name)