# common/services/shopify_service.py

import requests
from django.conf import settings
from .models import ShopifyConnection


class ShopifyService:
    """
    Service class to interact with the Shopify Admin REST API.

    This service provides methods for making authenticated requests to the Shopify API.
    """

    def __init__(self, shop_domain: str):
        """
        Initialize the ShopifyService instance with a specific Shopify store domain.

        Args:
            shop_domain (str): The domain of the Shopify store.
        """
        self.shop_domain = shop_domain
        self.access_token = self.get_access_token()

    def get_access_token(self) -> str:
        """
        Retrieve the access token for the authenticated session.

        Returns:
            str: The access token.
        """
        try:
            connection = ShopifyConnection.objects.get(shop_domain=self.shop_domain)
            return connection.access_token_encrypted
        except ShopifyConnection.DoesNotExist:
            raise ValueError("No Shopify connection found for the given domain.")

    def make_request(self, endpoint: str, method: str = 'GET', data=None) -> dict:
        """
        Make a request to the Shopify Admin REST API.

        Args:
            endpoint (str): The endpoint of the API.
            method (str): HTTP method (default is GET).
            data (dict): Data payload for POST requests.

        Returns:
            dict: JSON response from the API.
        """
        url = f'https://{self.shop_domain}/admin/api/2021-07/{endpoint}.json'
        headers = {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': self.access_token,
        }

        if method == 'POST':
            response = requests.post(url, json=data, headers=headers)
        elif method == 'PUT':
            response = requests.put(url, json=data, headers=headers)
        else:
            response = requests.get(url, headers=headers)

        response.raise_for_status()
        return response.json()

    def get_products(self) -> list:
        """
        Retrieve a list of products from the Shopify store.

        Returns:
            list: List of product dictionaries.
        """
        endpoint = 'products'
        response_data = self.make_request(endpoint)
        return response_data['products']

    def create_product(self, product_data: dict) -> dict:
        """
        Create a new product in the Shopify store.

        Args:
            product_data (dict): Data for the new product.

        Returns:
            dict: Newly created product dictionary.
        """
        endpoint = 'products'
        response_data = self.make_request(endpoint, method='POST', data=product_data)
        return response_data['product']

    def update_product(self, product_id: int, product_data: dict) -> dict:
        """
        Update an existing product in the Shopify store.

        Args:
            product_id (int): ID of the product to update.
            product_data (dict): Data for updating the product.

        Returns:
            dict: Updated product dictionary.
        """
        endpoint = f'products/{product_id}'
        response_data = self.make_request(endpoint, method='PUT', data=product_data)
        return response_data['product']

    def delete_product(self, product_id: int):
        """
        Delete a product from the Shopify store.

        Args:
            product_id (int): ID of the product to delete.
        """
        endpoint = f'products/{product_id}'
        self.make_request(endpoint, method='DELETE')