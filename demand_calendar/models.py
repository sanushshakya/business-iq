# demand_calendar/models.py

from django.db import models

class Event(models.Model):
    """
    Model representing an event for the Demand Calendar.

    Fields:
    - name: CharField representing the name of the event.
    - start_date: DateField representing the start date of the event.
    - end_date: DateField representing the end date of the event.
    - product_categories: ManyToManyField linking to associated ProductCategory objects.
    - demand_multiplier: DecimalField representing the multiplier for demand during the event.
    - demand_alerts: OneToOneField linking to an associated DemandAlert object.
    """
    name = models.CharField(max_length=255)
    start_date = models.DateField()
    end_date = models.DateField()
    product_categories = models.ManyToManyField('common.ProductCategory')
    demand_multiplier = models.DecimalField(max_digits=5, decimal_places=2)
    demand_alerts = models.OneToOneField('DemandAlert', on_delete=models.SET_NULL, null=True, blank=True)

class DemandAlert(models.Model):
    """
    Model representing an alert for a specific event.

    Fields:
    - event: ForeignKey linking to the associated Event object.
    - url: URLField representing the link to the demand alert.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    url = models.URLField(max_length=255)

# EOF