# inventory/test_breakdown_costs.py

"""G22: a breakdown spreads the input batch's landed cost over its product outputs, by weight or by value."""

from decimal import Decimal

from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, StockBatch, YieldTemplate
from inventory.test_breakdowns import BreakdownTestCase

D = Decimal


class CostAllocationTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        StockBatch.objects.filter(pk=self.input.pk).update(unit_cost=D('5.2000'))  # £/kg landed
        self.input.refresh_from_db()

    def costs(self, breakdown):
        return {line.name: line.allocated_cost for line in breakdown.lines.all()}

    def test_by_weight_sums_to_the_input_cost(self):
        breakdown, _ = self.record()
        self.assertEqual(breakdown.input_cost, D('52.00'))  # 10.000 kg × £5.20
        self.assertEqual(self.costs(breakdown), {'Fake Rump Steak': D('20.80'), 'Fake Beef Mince': D('31.20'),
                                                 'Bone': None, 'Fat and trim': None})
        steak = breakdown.lines.get(product=self.steak).output_batch
        self.assertEqual(steak.unit_cost, D('6.9333'))  # £20.80 / 3.000 kg

    def shin(self):
        # A fifth output: the fat shrinks so the template stays within 100%.
        self.out_fat.expected_percent = D('2')
        self.out_fat.save()
        return self.output(product=self.weighed('Fake Shin'), percent='5')

    def cost(self, per_kg):
        StockBatch.objects.filter(pk=self.input.pk).update(unit_cost=D(per_kg))

    def test_by_weight_remainder_goes_on_the_largest_output(self):
        shin = self.shin()
        self.cost('0.3332')
        breakdown, _ = self.record(lines=[(self.out_steak, '1.000'), (shin, '1.000'), (self.out_mince, '1.001')],
                                   input_kg=D('3.001'), loss_reason='')
        # £1.00 (3.001 × 0.3332 = 0.99993): 0.3332 → 0.33, 0.33, 0.3336 → 0.33 = 0.99; the 0.01 on mince (most kg)
        self.assertEqual(breakdown.input_cost, D('1.00'))
        self.assertEqual(self.costs(breakdown), {'Fake Rump Steak': D('0.33'), 'Fake Shin': D('0.33'),
                                                 'Fake Beef Mince': D('0.34')})

    def test_equal_outputs_remainder_on_the_first(self):
        shin = self.shin()
        self.cost('3.3333')
        breakdown, _ = self.record(lines=[(self.out_steak, '1.000'), (shin, '1.000'), (self.out_mince, '1.000')],
                                   input_kg=D('3.000'), loss_reason='')
        # £10.00 (9.9999): 3.33 × 3 = 9.99, the 0.01 on the first of the equal largest
        self.assertEqual(breakdown.input_cost, D('10.00'))
        self.assertEqual([line.allocated_cost for line in breakdown.lines.order_by('pk')],
                         [D('3.34'), D('3.33'), D('3.33')])

    def test_rounding_up_too_far_takes_the_penny_back_from_the_largest(self):
        shin = self.shin()
        self.cost('0.0050')
        breakdown, _ = self.record(lines=[(self.out_steak, '1.000'), (shin, '1.000'), (self.out_mince, '2.000')],
                                   input_kg=D('4.000'), loss_reason='')
        # £0.02: 0.005 → 0.01, 0.005 → 0.01, 0.01 = 0.03; −0.01 on mince (most kg)
        self.assertEqual(self.costs(breakdown), {'Fake Rump Steak': D('0.01'), 'Fake Shin': D('0.01'),
                                                 'Fake Beef Mince': D('0.00')})

    def test_taking_pennies_back_never_goes_below_zero(self):
        shin, flank = self.shin(), self.output(product=self.weighed('Fake Flank'), percent='1')
        self.cost('0.0050')
        lines = [(self.out_steak, '1.000'), (self.out_mince, '1.000'), (shin, '1.000'), (flank, '1.000')]
        breakdown, _ = self.record(lines=lines, input_kg=D('4.000'), loss_reason='')
        # £0.02 over four equal outputs: 0.005 → 0.01 each = 0.04; the 0.02 too much comes back a penny at a time
        costs = [line.allocated_cost for line in breakdown.lines.order_by('pk')]
        self.assertEqual((breakdown.input_cost, sum(costs)), (D('0.02'), D('0.02')))
        self.assertEqual(costs, [D('0.00'), D('0.00'), D('0.01'), D('0.01')])
        self.assertTrue(all(line.output_batch.unit_cost >= 0 for line in breakdown.lines.all()))

    def test_by_value(self):
        self.template.cost_method = 'value'
        self.template.save()
        Product.objects.filter(pk=self.steak.pk).update(price_per_kg=D('18.0000'))
        Product.objects.filter(pk=self.mince.pk).update(price_per_kg=D('8.9900'))
        breakdown, _ = self.record()
        # steak 3.000 × 18 = 54.00, mince 4.500 × 8.99 = 40.455: £52.00 × 54/94.455 = 29.728 → 29.73, 22.27
        self.assertEqual(self.costs(breakdown), {'Fake Rump Steak': D('29.73'), 'Fake Beef Mince': D('22.27'),
                                                 'Bone': None, 'Fat and trim': None})
        self.assertEqual(breakdown.input_cost, D('52.00'))

    def test_by_value_remainder_on_the_most_valuable(self):
        self.template.cost_method = 'value'
        self.template.save()
        shin = self.shin()
        Product.objects.filter(pk__in=[self.steak.pk, shin.product_id]).update(price_per_kg=D('1.0000'))
        Product.objects.filter(pk=self.mince.pk).update(price_per_kg=D('3.0000'))
        self.cost('0.2000')
        breakdown, _ = self.record(lines=[(self.out_steak, '2.000'), (self.out_mince, '1.000'), (shin, '2.000')],
                                   input_kg=D('5.000'), loss_reason='')
        # £1.00 by value 2 : 3 : 2 = 0.29, 0.43, 0.29 = 1.01; −0.01 on mince, the most valuable (not the most kg)
        self.assertEqual(self.costs(breakdown), {'Fake Rump Steak': D('0.29'), 'Fake Beef Mince': D('0.42'),
                                                 'Fake Shin': D('0.29')})

    def test_value_method_needs_a_price_per_kg(self):
        self.template.cost_method = 'value'
        self.template.save()
        Product.objects.filter(pk=self.mince.pk).update(price_per_kg=None)
        detail = self.refused()
        self.assertEqual(detail['template'], ['Fake Beef Mince has no price per kg, which spreading the cost by value '
                                              'needs. Set it, or spread this template by weight.'])

    def test_zero_or_negative_price_per_kg_counts_as_missing(self):
        self.template.cost_method = 'value'
        self.template.save()
        for price in ('0.0000', '-1.0000'):
            with self.subTest(price=price):
                Product.objects.filter(pk=self.mince.pk).update(price_per_kg=D(price))
                self.assertIn('Fake Beef Mince has no price per kg', self.refused()['template'][0])

    def test_value_method_without_a_cost_books_without_prices(self):
        self.template.cost_method = 'value'
        self.template.save()
        Product.objects.filter(pk=self.mince.pk).update(price_per_kg=None)
        self.cost_none()
        breakdown, _ = self.record()
        self.assertIsNone(breakdown.input_cost)

    def cost_none(self):
        StockBatch.objects.filter(pk=self.input.pk).update(unit_cost=None)

    def test_later_method_change_leaves_a_breakdown_alone(self):
        breakdown, _ = self.record()
        self.template.cost_method = 'value'
        self.template.save()
        breakdown.refresh_from_db()
        self.assertEqual((breakdown.cost_method, self.costs(breakdown)['Fake Rump Steak']), ('weight', D('20.80')))

    def test_voiding_a_costed_breakdown(self):
        from inventory.breakdowns import void_breakdown
        breakdown, _ = self.record()
        void_breakdown(breakdown, user=self.user, reason='Fake')
        breakdown.refresh_from_db()
        self.assertEqual((breakdown.status, breakdown.input_cost), ('voided', D('52.00')))
        self.assertEqual(StockBatch.objects.get(pk=self.input.pk).weight_kg, D('10.000'))

    def test_a_cost_too_large_to_store_is_refused(self):
        big = self.batch(self.side, kg='200.000', days=6, number='BIG-1')
        StockBatch.objects.filter(pk=big.pk).update(unit_cost=D('99999999.9999'))
        detail = self.refused(input_batch=StockBatch.objects.get(pk=big.pk), input_kg=D('200.000'),
                              lines=[(self.out_steak, '200.000')])
        self.assertEqual(detail['input_batch'], ['Batch BIG-1 costs too much to record (check its cost per kg).'])

    def test_no_cost_on_the_batch_means_no_allocation(self):
        StockBatch.objects.filter(pk=self.input.pk).update(unit_cost=None)
        breakdown, _ = self.record()
        self.assertIsNone(breakdown.input_cost)
        self.assertEqual(set(self.costs(breakdown).values()), {None})
        self.assertIsNone(breakdown.lines.get(product=self.steak).output_batch.unit_cost)

    def test_catch_weight_input_cost_is_its_kg(self):
        legs = self.weighed('Fake Lamb Legs', catch=True)
        template = YieldTemplate.objects.create(company=self.company, name='Fake legs', input_product=legs)
        out = self.output(product=self.mince, percent='90', template=template)
        batch = self.batch(legs, kg='10.000', days=6, number='LEGS-1', pieces=3)
        StockBatch.objects.filter(pk=batch.pk).update(unit_cost=D('6.0000'))
        batch.refresh_from_db()
        breakdown, _ = self.record(template=template, input_batch=batch, input_kg=None, input_pieces=1,
                                   lines=[(out, '3.300')])
        self.assertEqual((breakdown.input_kg, breakdown.input_cost), (D('3.333'), D('20.00')))  # 19.998 → 20.00
        self.assertEqual(breakdown.lines.get().allocated_cost, D('20.00'))


class CostApiTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_batch_takes_a_cost_and_the_template_a_method(self):
        response = self.api.patch(reverse('stockbatch-detail', args=[self.input.pk]), {'unit_cost': '5.2000'},
                                  format='json')
        self.assertEqual((response.status_code, response.json()['unit_cost']), (200, '5.2000'))
        self.assertEqual(self.api.patch(reverse('stockbatch-detail', args=[self.input.pk]), {'unit_cost': '-1'},
                                        format='json').status_code, 400)
        response = self.api.patch(reverse('yieldtemplate-detail', args=[self.template.pk]), {'cost_method': 'value'},
                                  format='json')
        self.assertEqual((response.status_code, response.json()['cost_method']), (200, 'value'))

    def test_only_staff_set_a_batch_cost(self):
        from common.tests.factories import UserFactory
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company, is_staff=False))
        url = reverse('stockbatch-detail', args=[self.input.pk])
        self.assertEqual(member.patch(url, {'unit_cost': '9.0000'}, format='json').status_code, 200)
        self.assertIsNone(StockBatch.objects.get(pk=self.input.pk).unit_cost)  # read-only for them
        self.assertEqual(self.api.patch(url, {'unit_cost': '9.0000'}, format='json').status_code, 200)
        self.assertEqual(StockBatch.objects.get(pk=self.input.pk).unit_cost, D('9.0000'))

    def test_another_companys_batch_cost_is_404(self):
        from common.tests.factories import CompanyFactory, UserFactory
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True))
        response = stranger.patch(reverse('stockbatch-detail', args=[self.input.pk]), {'unit_cost': '1'},
                                  format='json')
        self.assertEqual(response.status_code, 404)

    def test_breakdown_shows_costs(self):
        StockBatch.objects.filter(pk=self.input.pk).update(unit_cost=D('5.2000'))
        body = {'template': self.template.pk, 'location': self.butchery.pk, 'input_batch': self.input.pk,
                'input_kg': '10.000', 'loss_reason': 'Fake: drip',
                'lines': [{'output': self.out_steak.pk, 'weight_kg': '3.000'},
                          {'output': self.out_mince.pk, 'weight_kg': '4.500'},
                          {'output': self.out_bone.pk, 'weight_kg': '1.500'},
                          {'output': self.out_fat.pk, 'weight_kg': '0.650'}]}
        response = self.api.post(reverse('breakdown-list'), body, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data['input_cost'], data['cost_method']), ('52.00', 'weight'))
        self.assertEqual([line['allocated_cost'] for line in data['lines']], ['20.80', '31.20', None, None])
