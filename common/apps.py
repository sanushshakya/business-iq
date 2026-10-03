from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "common"

    def ready(self):
        """
        Register a company-scoped admin for every tenant-owned model that has no admin of its own.

        Runs after the admin app has imported every ``admin.py``, so explicit registrations win.
        """
        from django.apps import apps
        from django.contrib import admin

        from .tenancy import TENANT_LOOKUPS, TenantAdminMixin

        for label in TENANT_LOOKUPS:
            model = apps.get_model(label)
            if model in admin.site._registry:
                continue
            admin_class = type(f'{model.__name__}Admin', (TenantAdminMixin, admin.ModelAdmin), {})
            admin.site.register(model, admin_class)
