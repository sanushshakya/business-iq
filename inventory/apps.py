from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = "inventory"

    def ready(self):
        from . import signals  # noqa: F401  (default locations for new companies and branches)
