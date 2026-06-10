from django.db import models

# Create your models here.

class UserInvitation(models.Model):
    """
    Model representing a user invitation with company foreign key, invited email, role, token UUID,
    expiration time, and acceptance time.
    """

    company = models.ForeignKey('Company', on_delete=models.CASCADE)
    invited_email = models.EmailField(unique=True)
    role = models.CharField(max_length=100)
    token = models.UUIDField(unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Invitation for {self.invited_email} to join {self.company.name}"