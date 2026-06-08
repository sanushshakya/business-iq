from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

class CustomUserManager(BaseUserManager):
    """
    Custom user manager for the custom User model.
    """

    def create_user(self, email, company=None, branch=None, role=None, password=None, **extra_fields):
        """
        Create and save a new user with the given details.
        """
        if not email:
            raise ValueError('The Email must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, company=company, branch=branch, role=role, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """
        Create and save a new superuser with the given details.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password=password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model extending AbstractBaseUser.
    
    Fields:
    email (str): The user's email address.
    company (ForeignKey): The company that this user belongs to.
    branch (ForeignKey): The branch where this user works.
    role (str): The user's role within the system.
    is_active (bool): Whether the user account is active or not.
    is_verified (bool): Whether the user's email has been verified.
    date_joined (datetime): The timestamp when the user joined the system.
    """
    email = models.EmailField(unique=True)
    company = models.ForeignKey('Company', on_delete=models.CASCADE, null=True, blank=True, verbose_name='Company')
    branch = models.ForeignKey('Branch', on_delete=models.CASCADE, null=True, blank=True, verbose_name='Branch')
    role = models.CharField(max_length=50, verbose_name='Role')
    is_active = models.BooleanField(default=True, verbose_name='Is Active')
    is_verified = models.BooleanField(default=False, verbose_name='Is Verified')
    date_joined = models.DateTimeField(auto_now_add=True, verbose_name='Date Joined')

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['company', 'branch', 'role']

    class Meta:
        verbose_name = 'Custom User'
        verbose_name_plural = 'Custom Users'

    def __str__(self):
        return self.email