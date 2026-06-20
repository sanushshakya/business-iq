import requests
from celery import shared_task
from django.conf import settings
from .models import FreightAlert

@shared_task
def check_freight_rates():
    """
    Celery task to fetch current freight rates and create alerts if the rate has changed.
    
    This task will:
    1. Fetch current freight rates from an external API.
    2. Compare the current rate with the last recorded rate for each company.
    3. Create a FreightAlert instance if there is a significant change exceeding a predefined threshold.
    """
    # URL of the external API to fetch freight rates
    api_url = settings.FREIGHT_RATES_API_URL
    
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        
        rates_data = response.json()
        
        for rate_info in rates_data:
            company_id = rate_info['company_id']
            current_rate = rate_info['current_rate']
            
            # Fetch the last recorded freight rate from the database
            last_alert = FreightAlert.objects.filter(company_id=company_id).order_by('-id').first()
            
            if last_alert and abs(current_rate - last_alert.rate) >= settings.RATE_CHANGE_THRESHOLD:
                # Create a new alert if there is a significant change in rate
                FreightAlert.objects.create(
                    company_id=company_id,
                    rate=current_rate,
                    previous_rate=last_alert.rate
                )
    
    except requests.RequestException as e:
        # Log the error or handle it appropriately
        print(f"Error fetching freight rates: {e}")

# Example Celery Beat schedule configuration in settings.py
# CELERY_BEAT_SCHEDULE = {
#     'check_freight_rates_every_hour': {
#         'task': 'common.tasks.check_freight_rates',
#         'schedule': crontab(hour='*/1'),
#     },
# }