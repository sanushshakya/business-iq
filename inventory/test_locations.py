# inventory/test_locations.py

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from common.tests.factories import CompanyFactory
from tenants.models import Branch

VIRTUAL_CODES = {'IN-TRANSIT', 'SUPPLIER', 'CUSTOMER', 'WASTE', 'ADJUSTMENT'}


def codes(company):
    from inventory.models import Location

    return set(Location.objects.filter(company=company).values_list('code', flat=True))


class DefaultLocationTests(TestCase):
    def test_new_company_and_branch_get_defaults(self):
        from inventory.models import Location

        company = CompanyFactory(name='Fake Foods Ltd')
        self.assertEqual(codes(company), VIRTUAL_CODES)
        branch = Branch.objects.create(company=company, name='Fake High Street', address='1 Fake Street')
        shop = Location.objects.get(company=company, branch=branch)
        self.assertEqual((shop.code, shop.type, shop.name), (f'SHOP-{branch.pk}', 'shop_floor',
                                                              'Fake High Street shop floor'))
        self.assertTrue(shop.sellable and shop.online_sellable)
        for location in Location.objects.filter(company=company, code__in=VIRTUAL_CODES):
            self.assertIsNone(location.branch)
            self.assertFalse(location.sellable or location.online_sellable)
            self.assertTrue(location.is_virtual)

    def test_defaults_are_idempotent(self):
        from inventory.locations import ensure_default_locations
        from inventory.models import Location

        company = CompanyFactory()
        Branch.objects.create(company=company, name='Fake Branch', address='x')
        before = Location.objects.count()
        self.assertEqual(ensure_default_locations(company), 0)
        self.assertEqual(Location.objects.count(), before)

    def test_ensure_fills_in_what_is_missing(self):
        from inventory.locations import ensure_default_locations
        from inventory.models import Location

        company = CompanyFactory()
        branch = Branch.objects.create(company=company, name='Fake Branch', address='x')
        Location.objects.filter(company=company, code__in=['WASTE', f'SHOP-{branch.pk}']).delete()
        self.assertEqual(ensure_default_locations(company), 2)
        self.assertEqual(codes(company), VIRTUAL_CODES | {f'SHOP-{branch.pk}'})

    def test_a_renamed_shop_floor_is_not_duplicated(self):
        from inventory.locations import ensure_default_locations
        from inventory.models import Location

        company = CompanyFactory()
        branch = Branch.objects.create(company=company, name='Fake Branch', address='x')
        Location.objects.filter(branch=branch).update(code='FRONT')
        self.assertEqual(ensure_default_locations(company), 0)


class LocationRuleTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()

    def location(self, **fields):
        from inventory.models import Location

        defaults = {'company': self.company, 'code': 'BACK-1', 'name': 'Fake back store', 'type': 'back_store'}
        return Location(**{**defaults, **fields})

    def test_code_unique_per_company_not_globally(self):
        self.location(code='WH-1', type='warehouse').save()
        self.location(code='WH-1', type='warehouse', company=CompanyFactory()).save()  # another company: fine
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.location(code='WH-1', type='warehouse').save()

    def test_one_virtual_location_of_each_type_per_company(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.location(code='WASTE-2', type='waste').save()

    def test_online_sellable_requires_sellable(self):
        with self.assertRaises(ValidationError):
            self.location(online_sellable=True, sellable=False).full_clean()

    def test_virtual_location_cannot_be_sellable_or_have_branch(self):
        branch = Branch.objects.create(company=self.company, name='Fake Branch', address='x')
        with self.assertRaises(ValidationError):
            self.location(type='customer', code='CUST-2', sellable=True).full_clean()
        with self.assertRaises(ValidationError):
            self.location(type='in_transit', code='IT-2', branch=branch).full_clean()

    def test_shop_floor_needs_a_branch_of_the_same_company(self):
        with self.assertRaises(ValidationError):
            self.location(type='shop_floor', code='SHOP-X').full_clean()
        other = Branch.objects.create(company=CompanyFactory(), name='Other Branch', address='x')
        with self.assertRaises(ValidationError):
            self.location(type='shop_floor', code='SHOP-X', branch=other).full_clean()

    def test_code_is_upper_case_letters_digits_dashes(self):
        with self.assertRaises(ValidationError):
            self.location(code='back store!').full_clean()

    def test_location_is_tenant_scoped(self):
        from common.tenancy import TENANT_LOOKUPS

        self.assertEqual(TENANT_LOOKUPS['inventory.Location'], 'company')


class LocationApiTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        from common.tests.factories import UserFactory

        self.company = CompanyFactory(name='Fake Foods Ltd')
        self.other = CompanyFactory(name='Other Fake Ltd')
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.other_branch = Branch.objects.create(company=self.other, name='Other Street', address='x')
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)

    def url(self, pk=None):
        from django.urls import reverse

        return reverse('location-detail', args=[pk]) if pk else reverse('location-list')

    def create(self, **fields):
        body = {'code': 'WH-MAIN', 'name': 'Fake warehouse', 'type': 'warehouse', **fields}
        return self.api.post(self.url(), body, format='json')

    def test_create_list_and_patch(self):
        response = self.create(branch=self.branch.pk, sellable=True)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body['code'], body['type'], body['is_virtual']), ('WH-MAIN', 'warehouse', False))
        listed = {row['code'] for row in self.api.get(self.url()).json()['results']}
        self.assertEqual(listed, {'WH-MAIN', f'SHOP-{self.branch.pk}'} | VIRTUAL_CODES)
        patched = self.api.patch(self.url(body['id']), {'name': 'Fake main warehouse', 'is_active': False},
                                 format='json')
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertFalse(patched.json()['is_active'])

    def test_code_is_normalised(self):
        self.assertEqual(self.create(code=' wh-2 ').json()['code'], 'WH-2')

    def test_duplicate_code_is_400(self):
        self.create()
        self.assertEqual(self.create().status_code, 400)

    def test_other_companys_location_is_404(self):
        from inventory.models import Location

        theirs = Location.objects.get(company=self.other, code='WASTE')
        self.assertEqual(self.api.get(self.url(theirs.pk)).status_code, 404)
        self.assertEqual(self.api.patch(self.url(theirs.pk), {'name': 'x'}, format='json').status_code, 404)
        self.assertNotIn(theirs.pk, [row['id'] for row in self.api.get(self.url()).json()['results']])

    def test_company_in_body_is_ignored(self):
        from inventory.models import Location

        response = self.create(company=self.other.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Location.objects.get(pk=response.json()['id']).company, self.company)

    def test_branch_of_other_company_is_400(self):
        self.assertEqual(self.create(branch=self.other_branch.pk).status_code, 400)

    def test_rules_are_enforced(self):
        self.assertEqual(self.create(online_sellable=True, sellable=False).status_code, 400)
        self.assertEqual(self.create(type='butchery', code='BUTCH').status_code, 400)  # needs a branch
        self.assertEqual(self.create(type='waste', code='WASTE-2').status_code, 400)  # virtual: system only

    def test_virtual_locations_are_read_only(self):
        from inventory.models import Location

        waste = Location.objects.get(company=self.company, code='WASTE')
        self.assertEqual(self.api.patch(self.url(waste.pk), {'name': 'Bin'}, format='json').status_code, 400)
        self.assertEqual(self.api.delete(self.url(waste.pk)).status_code, 400)
        self.assertTrue(Location.objects.filter(pk=waste.pk).exists())

    def test_cannot_turn_a_location_virtual(self):
        location = self.create().json()
        self.assertEqual(self.api.patch(self.url(location['id']), {'type': 'customer'}, format='json').status_code, 400)

    def test_real_location_can_be_deleted(self):
        location = self.create().json()
        self.assertEqual(self.api.delete(self.url(location['id'])).status_code, 204)

    def test_filters(self):
        self.create()
        by_type = {r['code'] for r in self.api.get(self.url(), {'type': 'warehouse'}).json()['results']}
        self.assertEqual(by_type, {'WH-MAIN'})
        by_branch = {r['code'] for r in self.api.get(self.url(), {'branch': self.branch.pk}).json()['results']}
        self.assertEqual(by_branch, {f'SHOP-{self.branch.pk}'})
        sellable = {r['code'] for r in self.api.get(self.url(), {'sellable': 'true'}).json()['results']}
        self.assertEqual(sellable, {f'SHOP-{self.branch.pk}'})

    def test_non_staff_403(self):
        from rest_framework.test import APIClient

        from common.tests.factories import UserFactory

        api = APIClient()
        api.force_authenticate(UserFactory(company=self.company))
        self.assertEqual(api.get(self.url()).status_code, 403)

    def test_operator_403(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from authentication import refresh_tokens

        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Stock', 'stock')
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(api.get(self.url()).status_code, 403)

    def test_schema_lists_locations(self):
        from drf_spectacular.generators import SchemaGenerator

        paths = SchemaGenerator().get_schema(request=None, public=True)['paths']
        self.assertIn('/inventory/locations/', paths)
        self.assertIn('/inventory/locations/{id}/', paths)


class DefaultLocationsMigrationTests(TestCase):
    def setUp(self):
        import importlib

        from django.apps import apps

        self.migration = importlib.import_module('inventory.migrations.0010_default_locations')
        self.apps = apps
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake Branch', address='x')

    def test_forwards_creates_defaults_for_existing_companies_and_branches(self):
        from inventory.models import Location

        Location.objects.all().delete()  # as before G08: companies and branches without locations
        self.migration.forwards(self.apps, None)
        self.assertEqual(codes(self.company), VIRTUAL_CODES | {f'SHOP-{self.branch.pk}'})
        shop = Location.objects.get(code=f'SHOP-{self.branch.pk}')
        self.assertTrue(shop.sellable and shop.online_sellable)
        self.migration.forwards(self.apps, None)  # running again adds nothing
        self.assertEqual(Location.objects.filter(company=self.company).count(), 6)

    def test_backwards_removes_only_the_defaults(self):
        from inventory.models import Location

        Location.objects.create(company=self.company, code='WH-MAIN', name='Fake warehouse', type='warehouse')
        self.migration.backwards(self.apps, None)
        self.assertEqual(codes(self.company), {'WH-MAIN'})


class LocationReviewFixTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake Branch', address='x')

    def test_company_with_branches_can_be_deleted(self):
        from inventory.models import Location

        self.company.delete()
        self.assertFalse(Location.objects.filter(company_id=self.company.pk).exists())

    def test_branch_with_a_location_cannot_be_deleted_alone(self):
        from django.db.models import RestrictedError

        with self.assertRaises(RestrictedError):
            self.branch.delete()

    def test_new_branch_survives_a_taken_shop_code(self):
        from inventory.models import Location

        # A default shop floor turned into a back store keeps its SHOP-<n> code ...
        Location.objects.filter(branch=self.branch).update(type='back_store')
        # ... and staff made a location with the next branch's code.
        Location.objects.create(company=self.company, code=f'SHOP-{self.branch.pk + 1}', name='x', type='warehouse')
        second = Branch.objects.create(company=self.company, name='Second Branch', address='x')
        shop = Location.objects.get(branch=second, type='shop_floor')
        self.assertEqual(shop.code, f'SHOP-{second.pk}-2')

    def test_new_branch_does_not_bring_back_another_branchs_deleted_shop_floor(self):
        from inventory.models import Location

        Location.objects.filter(branch=self.branch).delete()  # a warehouse-only branch, on purpose
        Branch.objects.create(company=self.company, name='Second Branch', address='x')
        self.assertFalse(Location.objects.filter(branch=self.branch).exists())

    def test_admin_is_scoped_and_virtual_locations_are_read_only(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from common.tests.factories import UserFactory
        from inventory.models import Location

        model_admin = admin.site._registry[Location]
        request = RequestFactory().get('/admin/')
        request.user = UserFactory(company=self.company, is_staff=True)
        other = CompanyFactory()
        self.assertEqual(set(model_admin.get_queryset(request).values_list('company_id', flat=True)), {self.company.pk})
        waste = Location.objects.get(company=self.company, code='WASTE')
        self.assertFalse(model_admin.has_delete_permission(request, waste))
        self.assertIn('type', model_admin.get_readonly_fields(request, waste))
        self.assertNotIn(other.pk, model_admin.get_queryset(request).values_list('company_id', flat=True))
