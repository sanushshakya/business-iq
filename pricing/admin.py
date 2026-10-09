from django.contrib import admin

from .models import PricingPlan


@admin.register(PricingPlan)
class PricingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_per_month')
    search_fields = ('name',)
