from django.db import models

class Company(models.Model):
    """
    Model representing a company for which demo data will be seeded.

    Fields:
    - name: CharField representing the name of the company.
    - description: TextField representing a brief description of the company.
    - website_url: URLField representing the company's website URL.
    - contact_email: EmailField representing the primary contact email for the company.
    """

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField()
    website_url = models.URLField(validators=[URLValidator()])
    contact_email = models.EmailField()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Companies"
