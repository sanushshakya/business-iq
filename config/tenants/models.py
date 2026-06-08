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


class Branch(models.Model):
    """
    Model representing a branch of a company.
    
    Fields:
    company (ForeignKey): The company that this branch belongs to.
    name (str): The name of the branch.
    address (str): The address of the branch.
    is_active (bool): Whether the branch is active or not.
    created_at (datetime): The timestamp when the branch was created.
    """
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='branches', verbose_name='Company')
    name = models.CharField(max_length=255, verbose_name='Branch Name')
    address = models.TextField(verbose_name='Address')
    is_active = models.BooleanField(default=True, verbose_name='Is Active')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Branch'
        verbose_name_plural = 'Branches'

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class Till(models.Model):
    """
    Model representing a till at a branch.
    
    Fields:
    branch (ForeignKey): The branch where this till is located.
    number (int): The number of the till.
    is_active (bool): Whether the till is active or not.
    created_at (datetime): The timestamp when the till was created.
    """
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='tills', verbose_name='Branch')
    number = models.IntegerField(verbose_name='Till Number')
    is_active = models.BooleanField(default=True, verbose_name='Is Active')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Till'
        verbose_name_plural = 'Tills'

    def __str__(self):
        return f"{self.branch.name} - Till {self.number}"


# Migration for the Branch model
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Branch',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Branch Name')),
                ('address', models.TextField(verbose_name='Address')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('company', models.ForeignKey(on_delete=models.CASCADE, related_name='branches', to='tenants.company', verbose_name='Company')),
            ],
            options={
                'verbose_name': 'Branch',
                'verbose_name_plural': 'Branches',
            },
        ),
    ]


# Migration for the Till model
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0002_branch'),
    ]

    operations = [
        migrations.CreateModel(
            name='Till',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.IntegerField(verbose_name='Till Number')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('branch', models.ForeignKey(on_delete=models.CASCADE, related_name='tills', to='tenants.branch', verbose_name='Branch')),
            ],
            options={
                'verbose_name': 'Till',
                'verbose_name_plural': 'Tills',
            },
        ),
    ]


# Registering the models in Django admin
from django.contrib import admin

# Register your models here.
from .models import Company, Branch, Till

admin.site.register(Company)
admin.site.register(Branch)
admin.site.register(Till)