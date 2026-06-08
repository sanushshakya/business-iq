from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
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
    device_id (str): The unique identifier for the till device.
    is_active (bool): Whether the till is active or not.
    created_at (datetime): The timestamp when the till was created.
    """
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='tills', verbose_name='Branch')
    number = models.IntegerField(verbose_name='Till Number')
    device_id = models.CharField(max_length=100, unique=True, verbose_name='Device ID')
    is_active = models.BooleanField(default=True, verbose_name='Is Active')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Till'
        verbose_name_plural = 'Tills'

    def __str__(self):
        return f"{self.branch.name} - Till {self.number}"


class CustomUserManager(BaseUserManager):
    """
    Custom user manager for the custom User model.
    
    This manager extends BaseUserManager to include methods
    required to support creating users and superusers with email and password.
    """
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model extending AbstractBaseUser and including email as the username field.
    
    Fields:
    email (EmailField): The user's email address, used as the unique identifier.
    is_staff (bool): Whether the user has all permissions without explicitly assigning them.
    is_active (bool): Whether the user's account is currently active.
    created_at (datetime): The timestamp when the user was created.
    """
    email = models.EmailField(unique=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return self.email