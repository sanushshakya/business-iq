from django.contrib import admin
from django.utils import timezone

from .models import RefreshToken


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
