# common/services/shopify_service.py

"""
Shopify Admin GraphQL API client, limited to what the app needs: changing a product's price.

Shopify deprecated the REST product/variant endpoints (the REST Admin API has been "legacy" since
October 2024), so this uses GraphQL. Required access scope: ``write_products``.
Docs: https://shopify.dev/docs/api/admin-graphql/latest/mutations/productVariantsBulkUpdate
"""

import logging
from decimal import Decimal
from typing import List

import requests
from django.conf import settings

from sync.models import SHOP_DOMAIN_PATTERN, ShopifyConnection

logger = logging.getLogger(__name__)

PRODUCT_VARIANTS_QUERY = """
query ProductVariants($id: ID!, $after: String) {
  product(id: $id) {
    variants(first: 100, after: $after) {
      nodes { id }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

VARIANTS_BULK_UPDATE = """
mutation UpdateVariantPrices($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price }
    userErrors { field message }
  }
}
"""


class ShopifyError(Exception):
    """A request to Shopify failed, or Shopify rejected it."""


class ShopifyService:
    def __init__(self, shop_domain: str):
        # The domain ends up in a URL we send the access token to, so never trust an unvalidated value.
        if not SHOP_DOMAIN_PATTERN.match(shop_domain or ''):
            raise ValueError("shop_domain must look like 'my-store.myshopify.com'.")
        self.shop_domain = shop_domain
        self.access_token = self.get_access_token()

    def get_access_token(self) -> str:
        try:
            return ShopifyConnection.objects.get(shop_domain=self.shop_domain).access_token
        except ShopifyConnection.DoesNotExist:
            raise ValueError("No Shopify connection found for the given domain.")

    @property
    def endpoint(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json"

    def graphql(self, query: str, variables: dict) -> dict:
        """Run a query/mutation and return its ``data``; raise ShopifyError on any failure."""
        try:
            response = requests.post(
                self.endpoint,
                json={'query': query, 'variables': variables},
                headers={'Content-Type': 'application/json', 'X-Shopify-Access-Token': self.access_token},
                timeout=settings.HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ShopifyError(f"Shopify request failed: {exc}") from exc

        # GraphQL reports problems (including throttling) with HTTP 200 and an "errors" list.
        if payload.get('errors'):
            raise ShopifyError(f"Shopify returned errors: {payload['errors']}")
        return payload['data']

    @staticmethod
    def product_gid(product_id: int) -> str:
        return f"gid://shopify/Product/{int(product_id)}"

    def get_variant_ids(self, product_id: int) -> List[str]:
        """Global ids of every variant of a product (follows pagination)."""
        ids, cursor = [], None
        while True:
            data = self.graphql(PRODUCT_VARIANTS_QUERY, {'id': self.product_gid(product_id), 'after': cursor})
            if data.get('product') is None:
                raise ShopifyError(f"Product {product_id} was not found in {self.shop_domain}.")
            variants = data['product']['variants']
            ids += [node['id'] for node in variants['nodes']]
            if not variants['pageInfo']['hasNextPage']:
                return ids
            cursor = variants['pageInfo']['endCursor']

    def update_product_price(self, product_id: int, price: Decimal) -> List[dict]:
        """
        Set the price of every variant of a product. Returns the updated variants (``id``, ``price``).

        Raises ShopifyError if Shopify rejects any variant (nothing is partially applied).
        """
        variant_ids = self.get_variant_ids(product_id)
        if not variant_ids:
            raise ShopifyError(f"Product {product_id} has no variants to price.")

        formatted = f"{Decimal(price):.2f}"
        updated = []
        for start in range(0, len(variant_ids), 100):
            chunk = variant_ids[start:start + 100]
            data = self.graphql(VARIANTS_BULK_UPDATE, {
                'productId': self.product_gid(product_id),
                'variants': [{'id': variant_id, 'price': formatted} for variant_id in chunk],
            })
            result = data['productVariantsBulkUpdate']
            if result['userErrors']:
                raise ShopifyError(f"Shopify rejected the price update: {result['userErrors']}")
            updated += result['productVariants']
        return updated
