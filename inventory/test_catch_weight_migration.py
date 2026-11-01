# inventory/test_catch_weight_migration.py

"""G14 migration: every existing product is sold by each, and existing stock holds no kg."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [('inventory', '0016_requisitions')]
AFTER = [('inventory', '0017_catch_weight')]


class CatchWeightMigrationTests(TransactionTestCase):
    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return MigrationExecutor(connection).loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_existing_products_become_each(self):
        apps = self.migrate(BEFORE)
        company = apps.get_model('tenants', 'Company').objects.create(
            name='Fake Foods', registration_number='FAKE-G14', address='x')
        location = apps.get_model('inventory', 'Location').objects.create(
            company=company, code='WH-1', name='Fake warehouse', type='warehouse')
        product = apps.get_model('inventory', 'Product').objects.create(
            company=company, name='Fake Rice', description='x', price='1.00')
        apps.get_model('inventory', 'StockBatch').objects.create(
            product=product, batch_number='LOT-1', quantity=4, expiration_date='2030-01-31', location=location)

        apps = self.migrate(AFTER)
        product = apps.get_model('inventory', 'Product').objects.get(pk=product.pk)
        self.assertEqual((product.sold_by, product.is_catch_weight, product.price_per_kg), ('each', False, None))
        batch = apps.get_model('inventory', 'StockBatch').objects.get(product_id=product.pk)
        self.assertEqual((batch.quantity, batch.weight_kg), (4, 0))
