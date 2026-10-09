from django.contrib import admin

from .models import CulturalEvent


@admin.register(CulturalEvent)
class CulturalEventAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'location')
    search_fields = ('name',)
