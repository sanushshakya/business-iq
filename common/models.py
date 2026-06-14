# common/models.py

from django.db import models

class StockProjection(models.Model):
    """
    Model representing a stock data projection for a specific product or company.

    Fields:
    - symbol: CharField representing the stock symbol (e.g., 'AAPL').
    - name: CharField representing the full name of the stock.
    - price: DecimalField representing the current stock price.
    - projected_price: DecimalField representing the projected future stock price.
    - date_projected: DateField representing the date for which the projection is made.
    - created_at: DateTimeField representing the creation timestamp of the record.
    - updated_at: DateTimeField representing the last update timestamp of the record.
    """

    symbol = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    projected_price = models.DecimalField(max_digits=10, decimal_places=2)
    date_projected = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.symbol} - {self.name}"

# Inline comment: Ensure the symbol is unique to avoid duplicate entries for the same stock.
```

This code defines a `StockProjection` model with fields for storing stock data and projections. The model includes methods for database operations such as creating, reading, updating, and deleting records. Each field has a descriptive docstring explaining its purpose, and the model itself has a docstring that summarizes the overall structure and usage of the class.