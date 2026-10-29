from django.contrib import admin
from django.utils import timezone

from common.tenancy import TenantAdminMixin

from .admin_login import TwoFactorAdminAuthenticationForm
from .models import RefreshToken, TwoFactorEvent

# The admin asks for the second factor too, so it is not a password-only way in for admins.
admin.site.login_form = TwoFactorAdminAuthenticationForm
admin.site.login_template = 'authentication/admin_login.html'


@admin.register(RefreshToken)
class RefreshTokenAdmin(admin.ModelAdmin):
    """
    Read-only view of issued sessions for superusers, with a "revoke" action.

    Tokens are credentials-adjacent, so they cannot be added or edited, the hash is not shown, and
    company staff do not see them at all.
    """

    list_display = ('user', 'family', 'created_at', 'expires_at', 'revoked_at', 'rotated_at')
    list_filter = ('revoked_at',)
    search_fields = ('user__email',)
    list_select_related = ('user',)
    exclude = ('token_hash', 'password_stamp')
    actions = ['revoke_sessions']

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

    @admin.action(description='Revoke the selected sessions (whole token families)')
    def revoke_sessions(self, request, queryset):
        families = set(queryset.values_list('family', flat=True))
        count = RefreshToken.objects.filter(family__in=families, revoked_at__isnull=True).update(
            revoked_at=timezone.now())
        self.message_user(request, f"Revoked {count} active token(s) in {len(families)} session(s).")


@admin.register(TwoFactorEvent)
class TwoFactorEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    """The two-factor audit trail. Read-only: nobody can add, edit or remove entries."""

    list_display = ('created_at', 'user', 'kind', 'actor')
    list_filter = ('kind',)
    search_fields = ('user__email',)
    list_select_related = ('user', 'actor')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
