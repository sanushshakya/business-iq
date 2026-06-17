"""
common/tests/test_price_recommendation_service.py

This file contains unit tests for the price recommendation service, specifically focusing on the markdown tiers.
"""

from django.test import TestCase
from unittest.mock import patch
from datetime import timedelta
from decimal import Decimal
from common.models import Product
from common.services.price_recommendation_service import apply_decay_pricing, calculate_markdown_percentage

class TestMarkdownTiers(TestCase):
    """
    Unit tests for the price recommendation service markdown tiers.
    """

    def setUp(self):
        """
        Set up test data before each test method.
        """
        self.product = Product.objects.create(
            name="Test Product",
            description="A product for testing.",
            price=Decimal('100.00'),
            created_at=self.today_minus_days(7),
            updated_at=self.today_minus_days(7)
        )

    def tearDown(self):
        """
        Clean up test data after each test method.
        """
        self.product.delete()

    @staticmethod
    def today_minus_days(days):
        """
        Get the date and time for 'today' minus a specified number of days.

        Args:
            days (int): The number of days to subtract from 'today'.

        Returns:
            datetime.datetime: The date and time after subtracting the specified number of days.
        """
        from datetime import datetime
        return datetime.now() - timedelta(days=days)

    @patch('common.services.price_recommendation_service.calculate_markdown_percentage')
    def test_calculate_markdown_percentage(self, mock_calculate):
        """
        Test the calculate_markdown_percentage function with different shelf life remaining.
        """
        mock_calculate.return_value = Decimal('10.00')

        self.assertEqual(calculate_markdown_percentage(3), Decimal('10.00'))
        self.assertEqual(calculate_markdown_percentage(7), Decimal('20.00'))
        self.assertEqual(calculate_markdown_percentage(14), Decimal('30.00'))

    @patch('common.services.price_recommendation_service.apply_decay_pricing')
    def test_apply_decay_pricing(self, mock_apply):
        """
        Test the apply_decay_pricing function with different shelf life remaining.
        """
        mock_apply.return_value = None

        self.assertIsNone(apply_decay_pricing(self.product, 3))
        self.assertIsNone(apply_decay_pricing(self.product, 7))
        self.assertIsNone(apply_decay_pricing(self.product, 14))