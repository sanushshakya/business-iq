from django.contrib import admin

from .models import CulturalEvent


@admin.register(CulturalEvent)
class CulturalEventAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'location')
    list_filter = ('start_date',)
    search_fields = ('name',)
    date_hierarchy = 'start_date'
