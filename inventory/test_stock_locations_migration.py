# inventory/test_stock_locations_migration.py

"""G09 migrations: existing stock is placed in default locations without changing any quantity."""

from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [('inventory', '0011_location_branch_restrict')]
AFTER = [('inventory', '0014_stock_locations_required')]


class StockLocationsMigrationTests(TransactionTestCase):
    """Real schema migrations, so not a TestCase. Nothing here relies on seeded rows that a flush removes."""

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return MigrationExecutor(connection).loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def make_old_data(self):
        """Pre-G09 data: one company with two branches, one with none; batches and movements without locations."""
        apps = self.migrate(BEFORE)
        Company = apps.get_model('tenants', 'Company')
        Branch = apps.get_model('tenants', 'Branch')
        Location = apps.get_model('inventory', 'Location')
        Product = apps.get_model('inventory', 'Product')
        StockBatch = apps.get_model('inventory', 'StockBatch')
        StockMovement = apps.get_model('inventory', 'StockMovement')

        def company(name, reg, branches):
            c = Company.objects.create(name=name, registration_number=reg, address='x')
            for code, type_ in (('IN-TRANSIT', 'in_transit'), ('SUPPLIER', 'supplier'), ('CUSTOMER', 'customer'),
                                ('WASTE', 'waste'), ('ADJUSTMENT', 'adjustment')):
                Location.objects.create(company=c, code=code, name=code, type=type_)
            for name_ in branches:
                b = Branch.objects.create(company=c, name=name_, address='x')
                Location.objects.create(company=c, branch=b, code=f'SHOP-{b.pk}', name=f'{name_} shop floor',
                                        type='shop_floor', sellable=True, online_sellable=True)
            return c

        self.two = company('Fake Two Branches', 'FAKE2B', ['Fake First', 'Fake Second'])
        self.none = company('Fake No Branch', 'FAKE0B', [])
        on_hand = {}
        for c in (self.two, self.none):
            product = Product.objects.create(company=c, name=f'Fake rice {c.pk}', description='', price='1.00',
                                             stock_quantity=7)
            first = StockBatch.objects.create(product=product, batch_number='B1', quantity=5,
                                              expiration_date=date(2030, 1, 1))
            second = StockBatch.objects.create(product=product, batch_number='B2', quantity=2,
                                               expiration_date=date(2030, 2, 1))
            StockMovement.objects.create(batch=first, quantity=8, movement_type='inbound')
            StockMovement.objects.create(batch=first, quantity=3, movement_type='outbound')
            StockMovement.objects.create(batch=second, quantity=2, movement_type='inbound')
            on_hand[product.pk] = (7, {'B1': 5, 'B2': 2})
        return on_hand

    def test_existing_stock_is_placed_and_quantities_do_not_change(self):
        on_hand = self.make_old_data()
        apps = self.migrate(AFTER)
        Product = apps.get_model('inventory', 'Product')
        StockBatch = apps.get_model('inventory', 'StockBatch')
        StockMovement = apps.get_model('inventory', 'StockMovement')
        Location = apps.get_model('inventory', 'Location')
        Branch = apps.get_model('tenants', 'Branch')

        for product in Product.objects.all():
            batches = {b.batch_number: b.quantity for b in StockBatch.objects.filter(product=product)}
            self.assertEqual((product.stock_quantity, batches), on_hand[product.pk])

        first_branch = Branch.objects.filter(company_id=self.two.pk).order_by('pk').first()
        first_floor = Location.objects.get(branch=first_branch, type='shop_floor')
        self.assertEqual(set(StockBatch.objects.filter(product__company_id=self.two.pk)
                             .values_list('location_id', flat=True)), {first_floor.pk})

        main = Location.objects.get(company_id=self.none.pk, code='MAIN')
        self.assertEqual((main.type, main.sellable, main.online_sellable, main.branch_id), ('warehouse', True, True, None))
        self.assertEqual(set(StockBatch.objects.filter(product__company_id=self.none.pk)
                             .values_list('location_id', flat=True)), {main.pk})

        supplier = Location.objects.get(company_id=self.two.pk, type='supplier')
        customer = Location.objects.get(company_id=self.two.pk, type='customer')
        moves = StockMovement.objects.filter(batch__product__company_id=self.two.pk)
        for movement in moves.filter(movement_type='inbound'):
            self.assertEqual((movement.source_location_id, movement.destination_location_id),
                             (supplier.pk, first_floor.pk))
        for movement in moves.filter(movement_type='outbound'):
            self.assertEqual((movement.source_location_id, movement.destination_location_id),
                             (first_floor.pk, customer.pk))

    def test_migration_reverses(self):
        self.make_old_data()
        self.migrate(AFTER)
        apps = self.migrate(BEFORE)
        StockBatch = apps.get_model('inventory', 'StockBatch')
        self.assertEqual(sorted(StockBatch.objects.values_list('quantity', flat=True)), [2, 2, 5, 5])
