from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from common.tenancy import TenantAdminMixin

from .models import Branch, Company, CustomUser, Till

# Register models in Django admin with list_display showing parent relationships

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Company model.
    """
    list_display = ('name', 'registration_number')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(pk=request.user.company_id)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

@admin.register(Branch)
class BranchAdmin(TenantAdminMixin, admin.ModelAdmin):
    """
    Admin configuration for the Branch model.
    """
    list_display = ('name', 'company', 'address', 'is_active')

@admin.register(Till)
class TillAdmin(TenantAdminMixin, admin.ModelAdmin):
    """
    Admin configuration for the Till model.
    """
    list_display = ('branch', 'number')


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = CustomUser
        fields = ('email',)


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = '__all__'


@admin.register(CustomUser)
class CustomUserAdmin(TenantAdminMixin, UserAdmin):
    """
    Admin for the email-login user model.

    Superusers can manage everything. Company staff can only manage the (non-superuser) users of
    their own company, and only the fields that cannot be used to escalate privileges: email,
    password, active and staff flags. ``is_superuser``, groups, permissions and ``company`` are not
    shown to them (the company is stamped from their own on creation), and are ignored if posted.
    """

    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    model = CustomUser

    list_display = ('email', 'company', 'is_active', 'is_staff', 'is_superuser', 'created_at')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'company')
    search_fields = ('email',)
    ordering = ('email',)
    readonly_fields = ('last_login', 'created_at')
    filter_horizontal = ('groups', 'user_permissions')

    def get_list_filter(self, request):
        # Company staff only ever see their own company, and must not learn about other companies.
        return self.list_filter if request.user.is_superuser else ('is_active', 'is_staff')

    def get_list_display(self, request):
        columns = list(self.list_display)
        if not request.user.is_superuser:
            columns.remove('company')
            columns.remove('is_superuser')
        return columns

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser else queryset.exclude(is_superuser=True)

    def get_fieldsets(self, request, obj=None):
        full = request.user.is_superuser
        if obj is None:
            fields = ['email', 'password1', 'password2', 'is_staff']
            if full:
                fields.insert(3, 'company')
            return ((None, {'classes': ('wide',), 'fields': tuple(fields)}),)

        if not full:
            return (
                (None, {'fields': ('email', 'password')}),
                ('Permissions', {'fields': ('is_active', 'is_staff')}),
                ('Important dates', {'fields': ('last_login', 'created_at')}),
            )
        return (
            (None, {'fields': ('email', 'password')}),
            ('Company', {'fields': ('company',)}),
            ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
            ('Important dates', {'fields': ('last_login', 'created_at')}),
        )
