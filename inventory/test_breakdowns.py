# inventory/test_breakdowns.py

"""G21: recording and voiding a butchery breakdown — input = outputs + waste + loss exactly, through the ledger."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.test import TestCase

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.breakdowns import BreakdownError, record_breakdown, void_breakdown
from inventory.ledger import move, virtual_location
from inventory.models import Breakdown, Location, Product, StockBatch, StockMovement, YieldTemplate, YieldTemplateOutput
from tenants.models import Branch
from vat.services import uk_date

D = Decimal


class BreakdownTestCase(TestCase):
    def setUp(self):
        self.today = uk_date()
        self.company = CompanyFactory()
        self.user = UserFactory(company=self.company, is_staff=True)
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.butchery = Location.objects.create(company=self.company, branch=self.branch, code='BUTCH-1',
                                                name='Fake Butchery', type=Location.BUTCHERY, sellable=True)
        self.waste = virtual_location(self.company, Location.WASTE)
        self.adjustment = virtual_location(self.company, Location.ADJUSTMENT)
        self.side = self.weighed('Fake Side of Beef')
        self.steak = self.weighed('Fake Rump Steak')
        self.mince = self.weighed('Fake Beef Mince')
        self.template = YieldTemplate.objects.create(company=self.company, name='Fake side', input_product=self.side)
        self.out_steak = self.output(product=self.steak, percent='30', keeps_days=5)
        self.out_mince = self.output(product=self.mince, percent='45', keeps_days=2)
        self.out_bone = self.output(kind='waste', name='Bone', percent='15')
        self.out_fat = self.output(kind='waste', name='Fat and trim', percent='7')  # expected loss 3%
        self.input = self.batch(self.side, kg='10.000', days=6, number='SIDE-1')

    def weighed(self, name, catch=False):
        return ProductFactory(company=self.company, name=name, price=D('0.00'), stock_quantity=0, sold_by='weight',
                              is_catch_weight=catch, price_per_kg=D('9.0000'))

    def output(self, *, product=None, kind='product', name='', percent, keeps_days=None, template=None):
        template = template or self.template
        return YieldTemplateOutput.objects.create(template=template, kind=kind, product=product, name=name,
                                                  expected_percent=D(percent), keeps_days=keeps_days)

    def batch(self, product, *, kg, days, number, pieces=0, location=None):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), batch_number=number,
                                  location=location or self.butchery, expiration_date=self.today + timedelta(days=days))
        with transaction.atomic():
            move(batch, pieces, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER),
                 weight_kg=D(kg))
        batch.refresh_from_db()
        return batch

    def record(self, lines=None, **kwargs):
        if lines is None:
            lines = [(self.out_steak, '3.000'), (self.out_mince, '4.500'), (self.out_bone, '1.500'),
                     (self.out_fat, '0.650')]
        args = {'company': self.company, 'user': self.user, 'template': self.template, 'location': self.butchery,
                'input_batch': self.input, 'input_kg': D('10.000'), 'loss_reason': 'Fake: drip loss',
                'lines': [(o, D(kg), *rest) for o, kg, *rest in lines], **kwargs}
        return record_breakdown(**args)

    def refused(self, **kwargs):
        before = (Breakdown.objects.count(), StockMovement.objects.count())
        with self.assertRaises(BreakdownError) as caught:
            self.record(**kwargs)
        self.assertEqual((Breakdown.objects.count(), StockMovement.objects.count()), before)  # nothing booked
        return caught.exception.detail

    def kg(self, batch):
        return StockBatch.objects.get(pk=batch.pk).weight_kg


class RecordTests(BreakdownTestCase):
    def test_input_equals_outputs_waste_and_loss(self):
        breakdown, created = self.record()
        self.assertTrue(created)
        self.assertEqual((breakdown.reference, breakdown.status), ('BD-000001', 'completed'))
        self.assertEqual((breakdown.input_kg, breakdown.loss_kg), (D('10.000'), D('0.350')))
        self.assertEqual(breakdown.loss_reason, 'Fake: drip loss')
        lines = list(breakdown.lines.all())
        self.assertEqual(breakdown.input_kg,
                         sum(line.weight_kg for line in lines) + breakdown.loss_kg)
        moved = {row['destination_location']: row['kg'] for row in StockMovement.objects.filter(
            batch=self.input, movement_type='outbound').values('destination_location').annotate(kg=Sum('weight_kg'))}
        self.assertEqual(moved, {self.butchery.pk: D('7.500'), self.waste.pk: D('2.150'),
                                 self.adjustment.pk: D('0.350')})
        self.assertEqual(self.kg(self.input), D('0.000'))
        self.assertEqual(sum(moved.values()), D('10.000'))
        outputs = {line.product_id: line.output_batch for line in lines if line.product_id}
        self.assertEqual(self.kg(outputs[self.steak.pk]), D('3.000'))
        self.assertEqual(self.kg(outputs[self.mince.pk]), D('4.500'))
        for batch in outputs.values():
            self.assertEqual((batch.location_id, batch.batch_number), (self.butchery.pk, 'BD-000001'))
            inbound = StockMovement.objects.get(batch=batch)
            self.assertEqual((inbound.movement_type, inbound.source_location_id, inbound.breakdown_id),
                             ('inbound', self.butchery.pk, breakdown.pk))
        self.assertEqual(StockMovement.objects.filter(breakdown=breakdown).count(), 6)

    def test_part_of_a_batch(self):
        breakdown, _ = self.record(lines=[(self.out_mince, '3.900')], input_kg=D('4.000'))
        self.assertEqual(breakdown.loss_kg, D('0.100'))
        self.assertEqual(self.kg(self.input), D('6.000'))

    def test_loss_within_expected_needs_no_reason(self):
        breakdown, _ = self.record(lines=[(self.out_steak, '3.000'), (self.out_mince, '4.500'),
                                          (self.out_bone, '1.500'), (self.out_fat, '0.700')], loss_reason='')
        self.assertEqual((breakdown.loss_kg, breakdown.loss_reason), (D('0.300'), 'Expected loss (template)'))
        breakdown, _ = self.record(lines=[(self.out_steak, '2.000')], input_kg=D('2.000'),
                                   input_batch=self.batch(self.side, kg='2.000', days=6, number='SIDE-2'),
                                   loss_reason='')
        self.assertEqual((breakdown.loss_kg, breakdown.loss_reason), (D('0.000'), ''))

    def test_loss_over_expected_needs_a_reason(self):
        detail = self.refused(loss_reason='  ')
        self.assertEqual(detail['loss_reason'], [
            'Loss is 0.350 kg, more than the 0.300 kg the template expects; say why (e.g. trimmed for quality).'])

    def test_outputs_heavier_than_input_refused(self):
        detail = self.refused(lines=[(self.out_steak, '6.000'), (self.out_bone, '4.250')])
        self.assertEqual(detail['lines'], ['Outputs and waste weigh 0.250 kg more than the input; check the weights.'])
        self.assertEqual(self.kg(self.input), D('10.000'))

    def test_output_not_on_template_refused(self):
        other = YieldTemplate.objects.create(company=self.company, name='Other', input_product=self.side)
        stray = self.output(product=self.steak, percent='10', template=other)
        self.assertIn('lines', self.refused(lines=[(stray, '1.000')]))
        twice = self.refused(lines=[(self.out_steak, '1.000'), (self.out_steak, '1.000')])
        self.assertEqual(twice['lines'], ['Fake Rump Steak is listed twice.'])

    def test_no_product_output_refused(self):
        detail = self.refused(lines=[(self.out_bone, '1.000')], loss_reason='x')
        self.assertEqual(detail['lines'], ['Record what came out: at least one product with its weight.'])
        self.assertIn('lines', self.refused(lines=[(self.out_steak, '0.000')], loss_reason='x'))

    def test_output_expiry_capped(self):
        breakdown, _ = self.record()
        expiries = {line.product_id: line.expiration_date for line in breakdown.lines.all() if line.product_id}
        self.assertEqual(expiries, {self.steak.pk: self.today + timedelta(days=5),
                                    self.mince.pk: self.today + timedelta(days=2)})
        self.assertEqual(StockBatch.objects.get(product=self.mince, batch_number='BD-000001').expiration_date,
                         self.today + timedelta(days=2))
        short = self.batch(self.side, kg='5.000', days=3, number='SIDE-2')
        breakdown, _ = self.record(lines=[(self.out_steak, '4.900')], input_batch=short, input_kg=D('5.000'))
        self.assertEqual(breakdown.lines.get().expiration_date, self.today + timedelta(days=3))
        self.out_steak.keeps_days = None
        self.out_steak.save()
        breakdown, _ = self.record(lines=[(self.out_steak, '4.000')], input_batch=self.batch(
            self.side, kg='4.000', days=4, number='SIDE-3'), input_kg=D('4.000'))
        self.assertEqual(breakdown.lines.get().expiration_date, self.today + timedelta(days=4))

    def test_expiry_sent_earlier_is_used_later_is_refused(self):
        early = self.today + timedelta(days=1)
        breakdown, _ = self.record(lines=[(self.out_steak, '9.700', 0, early)])
        self.assertEqual(breakdown.lines.get().expiration_date, early)
        detail = self.refused(lines=[(self.out_steak, '0.500', 0, self.today + timedelta(days=6))],
                              input_batch=self.batch(self.side, kg='0.500', days=6, number='SIDE-2'),
                              input_kg=D('0.500'))
        self.assertIn('Fake Rump Steak can expire on', detail['lines'][0])
        past = self.refused(lines=[(self.out_steak, '0.500', 0, self.today - timedelta(days=1))],
                            input_batch=StockBatch.objects.get(batch_number='SIDE-2'), input_kg=D('0.500'))
        self.assertIn('lines', past)

    def test_expired_batch_refused(self):
        old = self.batch(self.side, kg='5.000', days=-1, number='OLD-1')
        detail = self.refused(input_batch=old, input_kg=D('5.000'), lines=[(self.out_steak, '5.000')])
        self.assertEqual(detail['input_batch'], [f'Batch OLD-1 expired on {old.expiration_date:%d %b %Y}; '
                                                 'it cannot be broken down.'])

    def test_input_expiring_today_allowed(self):
        today = self.batch(self.side, kg='5.000', days=0, number='TODAY-1')
        breakdown, _ = self.record(input_batch=today, input_kg=D('5.000'), lines=[(self.out_steak, '5.000')])
        self.assertEqual(breakdown.lines.get().expiration_date, self.today)

    def test_batch_at_another_location_refused(self):
        elsewhere = self.batch(self.side, kg='5.000', days=6, number='SHOP-SIDE', location=self.shop)
        detail = self.refused(input_batch=elsewhere, input_kg=D('5.000'), lines=[(self.out_steak, '5.000')])
        self.assertEqual(detail['input_batch'], [f'Batch SHOP-SIDE is at {self.shop.code}, not BUTCH-1; '
                                                 'transfer it first.'])

    def test_location_must_be_butchery(self):
        detail = self.refused(location=self.shop)
        self.assertEqual(detail['location'], [f'{self.shop.name} is not a butchery location.'])
        self.butchery.is_active = False
        self.butchery.save()
        self.assertIn('location', self.refused())

    def test_wrong_input_product_refused(self):
        mince = self.batch(self.mince, kg='5.000', days=6, number='MINCE-1')
        detail = self.refused(input_batch=mince, input_kg=D('5.000'), lines=[(self.out_steak, '5.000')])
        self.assertEqual(detail['input_batch'], ['This template breaks down Fake Side of Beef, not Fake Beef Mince.'])

    def test_inactive_template_refused(self):
        self.template.is_active = False
        self.template.save()
        self.assertEqual(self.refused()['template'], ['Fake side is not in use.'])

    def test_product_no_longer_weighed_refused(self):
        Product.objects.filter(pk=self.mince.pk).update(sold_by='each')
        detail = self.refused()
        self.assertIn('Fake Beef Mince is sold by each; a breakdown needs a weighed product.', detail['template'])

    def test_cannot_take_more_than_the_batch_holds(self):
        self.record(lines=[(self.out_steak, '6.000')], input_kg=D('6.000'))
        detail = self.refused(lines=[(self.out_steak, '5.000')], input_kg=D('5.000'))
        self.assertEqual(detail['input_kg'], ['Batch SIDE-1 holds 4.000 kg.'])

    def test_input_kg_must_be_positive_and_3dp(self):
        self.assertIn('input_kg', self.refused(input_kg=D('0.000')))
        self.assertIn('input_kg', self.refused(input_kg=None))

    def test_retry_does_not_double_book(self):
        ref = uuid.uuid4()
        first, created = self.record(client_ref=ref)
        movements = StockMovement.objects.count()
        again, created_again = self.record(client_ref=ref)
        self.assertEqual((again.pk, created, created_again), (first.pk, True, False))
        self.assertEqual(StockMovement.objects.count(), movements)

    def test_retry_racing_the_first_returns_it(self):
        # The second request read no breakdown before the first committed, then waited on the batch lock: once it
        # gets the lock it must find the first, not refuse because the batch is now empty.
        from unittest import mock
        from inventory import breakdowns
        ref = uuid.uuid4()
        first, _ = self.record(client_ref=ref)
        self.assertEqual(self.kg(self.input), D('0.000'))
        real_filter = Breakdown.objects.filter
        calls = []

        def stale_first_look(*args, **kwargs):
            calls.append(kwargs)
            queryset = real_filter(*args, **kwargs)
            return queryset.none() if len(calls) == 1 else queryset

        with mock.patch.object(breakdowns.Breakdown.objects, 'filter', side_effect=stale_first_look):
            again, created = self.record(client_ref=ref)
        self.assertEqual((again.pk, created), (first.pk, False))

    def test_batch_number_already_used_is_a_clear_error(self):
        StockBatchFactory(product=self.steak, batch_number='BD-000001', location=self.butchery, quantity=0,
                          weight_kg=D('0'))
        detail = self.refused()
        self.assertEqual(detail['lines'], ['Fake Rump Steak already has a batch BD-000001 at BUTCH-1; rename that '
                                           'batch first.'])

    def test_rows_of_another_company_refused_by_the_service(self):
        # The API scopes these; the service checks too (superusers are not scoped).
        other = CompanyFactory()
        their_side = ProductFactory(company=other, name='Their Side', sold_by='weight', price_per_kg=D('1.0000'))
        their_batch = StockBatchFactory(product=their_side, batch_number='THEIRS', quantity=0, weight_kg=D('5.000'),
                                        location=Location.objects.create(company=other, code='X', name='X',
                                                                         type='warehouse'))
        self.assertEqual(self.refused(input_batch=their_batch)['input_batch'], ['Invalid pk - object does not exist.'])
        their_template = YieldTemplate.objects.create(company=other, name='Theirs', input_product=their_side)
        self.assertEqual(self.refused(template=their_template)['template'], ['Invalid pk - object does not exist.'])

    def test_lost_race_on_client_ref_returns_the_first(self):
        from unittest import mock
        from django.db import IntegrityError
        from inventory import breakdowns
        ref = uuid.uuid4()
        first, _ = self.record(client_ref=ref, lines=[(self.out_steak, '5.000')], input_kg=D('5.000'))
        with mock.patch.object(breakdowns, '_record', side_effect=IntegrityError('unique client_ref')), \
                mock.patch.object(breakdowns.Breakdown.objects, 'filter', side_effect=[
                    Breakdown.objects.none(), Breakdown.objects.filter(pk=first.pk)]):
            again, created = self.record(client_ref=ref)
        self.assertEqual((again.pk, created), (first.pk, False))

    def test_catch_weight_input_takes_pieces_at_average_kg(self):
        legs = self.weighed('Fake Lamb Legs', catch=True)
        template = YieldTemplate.objects.create(company=self.company, name='Fake legs', input_product=legs)
        out = self.output(product=self.mince, percent='90', template=template)
        batch = self.batch(legs, kg='10.000', days=6, number='LEGS-1', pieces=3)
        breakdown, _ = self.record(template=template, input_batch=batch, input_kg=None, input_pieces=1,
                                   lines=[(out, '3.300')])
        self.assertEqual((breakdown.input_pieces, breakdown.input_kg, breakdown.loss_kg),
                         (1, D('3.333'), D('0.033')))
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, batch.weight_kg), (2, D('6.667')))
        detail = self.refused(template=template, input_batch=batch, input_pieces=3, input_kg=None,
                              lines=[(out, '1.000')])
        self.assertEqual(detail['input_pieces'], ['Batch LEGS-1 holds 2 piece(s).'])

    def test_catch_weight_last_pieces_take_all_kg(self):
        legs = self.weighed('Fake Lamb Legs', catch=True)
        template = YieldTemplate.objects.create(company=self.company, name='Fake legs', input_product=legs)
        out = self.output(product=self.mince, percent='100', template=template)
        batch = self.batch(legs, kg='10.000', days=6, number='LEGS-1', pieces=3)
        breakdown, _ = self.record(template=template, input_batch=batch, input_kg=None, input_pieces=3,
                                   lines=[(out, '10.000')])
        self.assertEqual(breakdown.input_kg, D('10.000'))
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, batch.weight_kg), (0, D('0.000')))
        self.assertEqual(StockMovement.objects.get(batch=batch, breakdown=breakdown).quantity, 3)

    def test_catch_weight_output_needs_pieces(self):
        packs = self.weighed('Fake Steak Packs', catch=True)
        out = self.output(product=packs, percent='20', keeps_days=4)
        detail = self.refused(lines=[(out, '2.000')])
        self.assertEqual(detail['lines'], ['Fake Steak Packs: say how many pieces.'])
        detail = self.refused(lines=[(self.out_steak, '2.000', 3)])
        self.assertEqual(detail['lines'], ['Fake Rump Steak is sold loose; leave the pieces out.'])
        breakdown, _ = self.record(lines=[(out, '9.800', 4)])
        line = breakdown.lines.get()
        self.assertEqual((line.pieces, line.output_batch.quantity, line.output_batch.weight_kg),
                         (4, 4, D('9.800')))

    def test_input_pieces_for_loose_and_kg_for_catch_weight_refused(self):
        self.assertIn('input_pieces', self.refused(input_pieces=2))
        legs = self.weighed('Fake Lamb Legs', catch=True)
        template = YieldTemplate.objects.create(company=self.company, name='Fake legs', input_product=legs)
        out = self.output(product=self.mince, percent='90', template=template)
        batch = self.batch(legs, kg='10.000', days=6, number='LEGS-1', pieces=3)
        self.assertIn('input_kg', self.refused(template=template, input_batch=batch, input_pieces=1,
                                               lines=[(out, '3.000')]))


class VoidTests(BreakdownTestCase):
    def held(self, batch):
        batch = StockBatch.objects.get(pk=batch.pk)
        return batch.quantity, batch.weight_kg

    def test_void_restores_every_batch(self):
        breakdown, _ = self.record()
        outputs = [line.output_batch for line in breakdown.lines.all() if line.output_batch_id]
        voided = void_breakdown(breakdown, user=self.user, reason='Fake: recorded on the wrong batch')
        self.assertEqual((voided.status, voided.void_reason, voided.voided_by), ('voided', 'Fake: recorded on the '
                                                                                'wrong batch', self.user))
        self.assertIsNotNone(voided.voided_at)
        self.assertEqual(self.held(self.input), (0, D('10.000')))
        for batch in outputs:
            self.assertEqual(self.held(batch), (0, D('0.000')))
        for batch in [self.input, *outputs]:
            signed = sum((m.weight_kg if m.movement_type == 'inbound' else -m.weight_kg)
                         for m in StockMovement.objects.filter(batch=batch, breakdown=breakdown))
            self.assertEqual(signed, D('0.000'), batch)
        self.assertEqual(StockMovement.objects.filter(breakdown=breakdown).count(), 12)

    def test_void_restores_catch_weight_pieces(self):
        legs = self.weighed('Fake Lamb Legs', catch=True)
        packs = self.weighed('Fake Leg Steaks', catch=True)
        template = YieldTemplate.objects.create(company=self.company, name='Fake legs', input_product=legs)
        out = self.output(product=packs, percent='90', template=template)
        batch = self.batch(legs, kg='10.000', days=6, number='LEGS-1', pieces=3)
        breakdown, _ = self.record(template=template, input_batch=batch, input_kg=None, input_pieces=2,
                                   lines=[(out, '6.500', 8)])
        void_breakdown(breakdown, user=self.user, reason='Fake')
        self.assertEqual(self.held(batch), (3, D('10.000')))
        self.assertEqual(self.held(breakdown.lines.get().output_batch), (0, D('0.000')))

    def test_void_refused_once_sold(self):
        breakdown, _ = self.record()
        mince = breakdown.lines.get(product=self.mince).output_batch
        with transaction.atomic():
            move(mince, 0, 'outbound', counterpart=virtual_location(self.company, Location.CUSTOMER),
                 weight_kg=D('0.500'))
        movements = StockMovement.objects.count()
        with self.assertRaises(BreakdownError) as caught:
            void_breakdown(breakdown, user=self.user, reason='Fake')
        self.assertEqual(caught.exception.detail['detail'], [
            '0.500 kg of Fake Beef Mince from BD-000001 has gone (sold or moved); it cannot be voided.'])
        self.assertEqual(StockMovement.objects.count(), movements)
        breakdown.refresh_from_db()
        self.assertEqual(breakdown.status, 'completed')

    def test_void_refused_once_a_piece_sold(self):
        packs = self.weighed('Fake Steak Packs', catch=True)
        out = self.output(product=packs, percent='20', keeps_days=4)
        breakdown, _ = self.record(lines=[(out, '9.800', 4)])
        made = breakdown.lines.get().output_batch
        with transaction.atomic():
            move(made, 1, 'outbound', counterpart=virtual_location(self.company, Location.CUSTOMER),
                 weight_kg=D('0.000'))
        with self.assertRaises(BreakdownError) as caught:
            void_breakdown(breakdown, user=self.user, reason='Fake')
        self.assertEqual(caught.exception.detail['detail'], [
            '1 piece(s) of Fake Steak Packs from BD-000001 has gone (sold or moved); it cannot be voided.'])

    def test_void_twice_refused(self):
        breakdown, _ = self.record()
        void_breakdown(breakdown, user=self.user, reason='Fake')
        with self.assertRaises(BreakdownError):
            void_breakdown(breakdown, user=self.user, reason='Fake')

    def test_void_after_a_product_changed_kind_is_a_clear_error(self):
        breakdown, _ = self.record()
        Product.objects.filter(pk=self.side.pk).update(sold_by='each')  # allowed: its batch is empty
        with self.assertRaises(BreakdownError) as caught:
            void_breakdown(breakdown, user=self.user, reason='Fake')
        self.assertIn('Fake Side of Beef', caught.exception.detail['detail'][0])
        breakdown.refresh_from_db()
        self.assertEqual(breakdown.status, 'completed')

    def test_breakdowns_are_not_demand(self):
        from inventory.projections import average_daily_demand
        legs = self.weighed('Fake Lamb Legs', catch=True)
        packs = self.weighed('Fake Leg Steaks', catch=True)
        template = YieldTemplate.objects.create(company=self.company, name='Fake legs', input_product=legs)
        out = self.output(product=packs, percent='90', template=template)
        batch = self.batch(legs, kg='10.000', days=6, number='LEGS-1', pieces=3)
        breakdown, _ = self.record(template=template, input_batch=batch, input_kg=None, input_pieces=2,
                                   lines=[(out, '6.500', 8)])
        void_breakdown(breakdown, user=self.user, reason='Fake')
        self.assertEqual((average_daily_demand(legs, days=7), average_daily_demand(packs, days=7)), (0, 0))

    def test_void_needs_a_reason(self):
        breakdown, _ = self.record()
        with self.assertRaises(BreakdownError) as caught:
            void_breakdown(breakdown, user=self.user, reason=' ')
        self.assertIn('reason', caught.exception.detail)
