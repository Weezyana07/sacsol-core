# accounts/admin.py
from django.contrib import admin

class SuperuserOnlyAdminSite(admin.AdminSite):
    def has_permission(self, request):
        return bool(request.user and request.user.is_active and request.user.is_superuser)

admin_site = SuperuserOnlyAdminSite(name="superadmin")
# register your models with admin_site instead of admin.site