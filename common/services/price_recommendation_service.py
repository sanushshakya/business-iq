# common/services/price_recommendation_service.py

import logging

class PriceRecommendationService:
    """
    Service class to compute recommended retail prices based on landed cost and margin percent.

    This service can be used throughout the application to ensure that all price computations are handled consistently.
    """

    def __init__(self, logger_name='PriceRecommendationService'):
        # Initialize a logger for logging price changes
        self.logger = logging.getLogger(logger_name)

    def calculate_recommended_price(self, landed_cost, margin_percent):
        """
        Calculate the recommended retail price based on the landed cost and margin percent.

        Args:
            landed_cost (float): The total cost of the product including all expenses.
            margin_percent (float): The desired profit margin as a percentage.

        Returns:
            float: The calculated recommended retail price.
        """
        if landed_cost <= 0 or margin_percent <= 0:
            raise ValueError("Landed cost and margin percent must be greater than zero.")

        # Calculate the recommended retail price
        recommended_price = landed_cost * (1 + margin_percent / 100)

        # Log the price change
        self.logger.info(f"Calculated recommended price: {recommended_price} based on landed cost: {landed_cost} and margin percent: {margin_percent}")

        return recommended_price