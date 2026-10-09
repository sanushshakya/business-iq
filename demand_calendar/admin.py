from django.contrib import admin

from .models import DemandAlert, Event


class DemandAlertInline(admin.TabularInline):
    model = DemandAlert
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Shared reference data: which categories an event lifts demand for, and by how much."""

    list_display = ('name', 'start_date', 'end_date', 'demand_multiplier')
    list_filter = ('start_date',)
    search_fields = ('name',)
    filter_horizontal = ('product_categories',)
    inlines = [DemandAlertInline]
