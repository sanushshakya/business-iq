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

class Branch(models.Model):
    """
    Model representing a branch where products are stored and managed.

    Fields:
    - name: CharField representing the name of the branch.
    - address: TextField representing the address of the branch.
    """

    name = models.CharField(max_length=255)
    address = models.TextField()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Branch"
        verbose_name_plural = "Branches"

class DemandAlert(models.Model):
    """
    Model representing a demand alert for a product.

    Fields:
    - product: ForeignKey to the Product model, linking the alert to a specific product.
    - threshold_quantity: IntegerField representing the minimum stock quantity that triggers an alert.
    """

    product = models.ForeignKey('demo_data.Product', on_delete=models.CASCADE)
    threshold_quantity = models.IntegerField()

    def __str__(self):
        return f"Demand Alert for {self.product.name}"

    class Meta:
        verbose_name = "Demand Alert"
        verbose_name_plural = "Demand Alerts"

class PriceChangeLog(models.Model):
    """
    Model representing a log entry for price changes of products.

    Fields:
    - product: ForeignKey to the Product model, linking the log entry to a specific product.
    - old_price: DecimalField representing the previous price of the product.
    - new_price: DecimalField representing the new price of the product.
    - change_date: DateTimeField representing when the price change occurred.
    """

    product = models.ForeignKey('demo_data.Product', on_delete=models.CASCADE)
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    change_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Price Change for {self.product.name} from {self.old_price} to {self.new_price}"

    class Meta:
        verbose_name = "Price Change Log"
        verbose_name_plural = "Price Change Logs"