# common/services/cost_calculation_service.py

from django.conf import settings

class CostCalculationService:
    """
    Service class for calculating various costs associated with a product or transaction.
    """

    def calculate_landed_cost(self, product, quantity):
        """
        Calculate the total landed cost of a product based on its price and other factors.

        Args:
            product (Product): The product object for which to calculate the landed cost.
            quantity (int): The quantity of the product being considered.

        Returns:
            float: The calculated landed cost.
        """
        # Base landed cost is the product's price multiplied by the quantity
        base_cost = product.price * quantity

        # Add customs duty based on the commodity code
        customs_duty_rate = self.get_customs_duty_rate(product.commodity_code)
        customs_duty_amount = base_cost * customs_duty_rate

        # Calculate total landed cost
        total_landed_cost = base_cost + customs_duty_amount

        return total_landed_cost

    def get_customs_duty_rate(self, commodity_code):
        """
        Retrieve the customs duty rate for a given commodity code.

        Args:
            commodity_code (str): The commodity code to retrieve the duty rate for.

        Returns:
            float: The customs duty rate.
        """
        # Mock implementation using a dictionary. In production, this would likely
        # interact with an external API or database to fetch the correct rate.
        duty_rates = {
            '123': 0.15,
            '456': 0.10,
            '789': 0.20,
        }

        return duty_rates.get(commodity_code, settings.DEFAULT_CUSTOMS_DUTY_RATE)