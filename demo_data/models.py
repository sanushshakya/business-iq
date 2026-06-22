from django.db import models

class Branch(models.Model):
    """
    Model representing a branch for which demo data will be seeded.

    Fields:
    - name: CharField representing the name of the branch.
    - address: TextField representing the physical address of the branch.
    - city: CharField representing the city where the branch is located.
    - state: CharField representing the state or province where the branch is located.
    - zip_code: CharField representing the postal code of the branch's address.
    - country: CharField representing the country where the branch is located.
    - company: ForeignKey to the Company model, linking the branch to a specific company.
    """

    name = models.CharField(max_length=255)
    address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    zip_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100)
    company = models.ForeignKey('demo_data.Company', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state}"

    class Meta:
        verbose_name = "Branch"
        verbose_name_plural = "Branches"

class Till(models.Model):
    """
    Model representing a till for which demo data will be seeded.

    Fields:
    - branch: ForeignKey to the Branch model, linking the till to a specific branch.
    - serial_number: CharField representing the unique serial number of the till.
    - status: CharField representing the current status of the till (e.g., "active", "offline").
    - last_rebooted_at: DateTimeField representing the timestamp when the till was last rebooted.
    """

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('offline', 'Offline'),
    ]

    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    serial_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    last_rebooted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.branch.name} - {self.serial_number}"

    class Meta:
        verbose_name = "Till"
        verbose_name_plural = "Tills"