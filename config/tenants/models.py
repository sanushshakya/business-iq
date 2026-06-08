from django.db import models

# Create your models here.

class Company(models.Model):
    """
    Model representing a company with essential details.
    
    Fields:
    name (str): The name of the company.
    registration_number (str): The registration number of the company.
    address (str): The address of the company.
    created_at (datetime): The timestamp when the company was created.
    """
    name = models.CharField(max_length=255, verbose_name='Company Name')
    registration_number = models.CharField(max_length=100, unique=True, verbose_name='Registration Number')
    address = models.TextField(verbose_name='Address')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Company'
        verbose_name_plural = 'Companies'

    def __str__(self):
        return self.name
```

```python
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Company',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Company Name')),
                ('registration_number', models.CharField(max_length=100, unique=True, verbose_name='Registration Number')),
                ('address', models.TextField(verbose_name='Address')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
            ],
            options={
                'verbose_name': 'Company',
                'verbose_name_plural': 'Companies',
            },
        ),
    ]
```

```python
from django.contrib import admin

# Register your models here.
from .models import Company

admin.site.register(Company)