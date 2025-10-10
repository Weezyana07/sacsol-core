# accounts/apps.py
from django.apps import AppConfig

class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # Connect signals on app load
        from django.db.models.signals import post_migrate
        from .signals import ensure_groups  # noqa
        post_migrate.connect(ensure_groups, sender=self)