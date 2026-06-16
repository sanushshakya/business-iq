# common/services/hmrctariff_service.py

import requests
from django.conf import settings

class HMRCTariffService:
    """
    Class to fetch import duty rate from HMRC API.

    This class provides a method to make an HTTP request to the HMRC Tariff Service API and retrieve the import duty rate for a given product.
    """

    def __init__(self, api_key: str = None):
        """
        Initialize the HMRCTariffService with an optional API key.

        Args:
            api_key (str): Optional API key for authentication with the HMRC Tariff Service. If not provided, it will use the one configured in Django settings.
        """
        self.api_key = api_key or settings.HMRC_API_KEY
        if not self.api_key:
            raise ValueError("HMRC API key is required.")

    def fetch_tariff_rate(self, product_code: str) -> dict:
        """
        Fetch import duty rate from HMRC Tariff Service.

        Args:
            product_code (str): The code of the product for which to retrieve the import duty rate.

        Returns:
            dict: A dictionary containing the import duty rate information or an error message if something went wrong.
        """
        url = f"{settings.HMRC_API_URL}/tariff-rate/{product_code}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # Raise an HTTPError for bad responses
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e)}