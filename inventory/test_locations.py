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
