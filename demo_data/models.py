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