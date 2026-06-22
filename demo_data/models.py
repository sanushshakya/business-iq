from django.db import models

class Product(models.Model):
    """
    Model representing a product for which demo data will be seeded.

    Fields:
    - name: CharField representing the name of the product.
    - description: TextField representing a brief description of the product.
    - price: DecimalField representing the price of the product.
    - stock_quantity: IntegerField representing the current quantity in stock.
    - category: CharField representing the category of the product (e.g., "Fruits", "Vegetables").
    - branch: ForeignKey to the Branch model, linking the product to a specific branch.
    """

    CATEGORY_CHOICES = [
        ('Fruits', 'Fruits'),
        ('Vegetables', 'Vegetables'),
        # Add more categories as needed
    ]

    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.IntegerField(default=0)
    category = models.CharField(max_length=100, choices=CATEGORY_CHOICES, default='Fruits')
    branch = models.ForeignKey('demo_data.Branch', on_delete=models.CASCADE)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"

class StockMovement(models.Model):
    """
    Model representing a stock movement for a product.

    Fields:
    - product: ForeignKey to the Product model, linking the movement to a specific product.
    - movement_type: CharField representing whether the movement is an 'in' or 'out'.
    - quantity: IntegerField representing the quantity moved.
    - timestamp: DateTimeField representing when the movement occurred.
    """

    MOVEMENT_CHOICES = [
        ('in', 'In'),
        ('out', 'Out'),
    ]

    product = models.ForeignKey('demo_data.Product', on_delete=models.CASCADE)
    movement_type = models.CharField(max_length=3, choices=MOVEMENT_CHOICES)
    quantity = models.IntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - {self.movement_type} - {self.quantity}"

    class Meta:
        verbose_name = "Stock Movement"
        verbose_name_plural = "Stock Movements"