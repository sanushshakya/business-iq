"""G58: writes the shared promotion fixtures: `python3 scripts/gen_promotion_fixtures.py promotions/promotion_fixtures.json`.
Expected values are worked out by hand (each case's note shows the sums); copy the file to fe and update both hashes."""
import json
import sys


def line(key, product, gross, quantity=1, category=None, weighed=False, label=False):
    return {'key': key, 'product': product, 'category': category, 'quantity': quantity, 'gross': gross,
            'weighed': weighed, 'label': label}

def promo(id, kind, name, priority=0, stacks=False, buy=None, get=None, price=None, percent=None, products=(),
          categories=(), groups=()):
    return {'id': id, 'name': name, 'kind': kind, 'priority': priority, 'stacks': stacks, 'buy_quantity': buy,
            'get_quantity': get, 'deal_price': price, 'percent': percent, 'products': list(products),
            'categories': list(categories), 'groups': [list(g) for g in groups]}

def case(name, note, lines, promotions, discounts, applied):
    return {'name': name, 'note': note, 'lines': lines, 'promotions': promotions,
            'expected': {'discounts': discounts,
                         'applied': [{'promotion': p, 'times': t, 'saving': s} for p, t, s in applied]}}

def MB(**k):  # noqa: N802
    return promo(1, 'multi_buy', '3 for £5', buy=3, price='5.00', **k)


def MEAL(**k):  # noqa: N802
    return promo(2, 'meal_deal', 'Meal deal', price='3.50', groups=[[1, 2], [3], [4]], **k)



cases = [
    case('multi_buy_exact', '3 x 1.99 = 5.97 for 5.00: 97p off',
         [line('a', 1, '5.97', 3)], [MB(products=[1])], {'a': '0.97'}, [(1, 1, '0.97')]),
    case('multi_buy_97p_over_three_lines', '97p by price: 32.33p each; the spare penny to the first line',
         [line('a', 1, '1.99'), line('b', 2, '1.99'), line('c', 3, '1.99')], [MB(products=[1, 2, 3])],
         {'a': '0.33', 'b': '0.32', 'c': '0.32'}, [(1, 1, '0.97')]),
    case('multi_buy_partial', '4 x 2.00: one set of 3 (1.00 off), one left at full price',
         [line('a', 1, '8.00', 4)], [MB(products=[1])], {'a': '1.00'}, [(1, 1, '1.00')]),
    case('multi_buy_mixed_most_expensive_first',
         'units 2.50,2.50,1.50 x4: set 1 = 6.50 -> 1.50 off (57.69p,57.69p,34.62p -> 58,58,34); set 2 = 4.50 saves nothing',
         [line('a', 1, '5.00', 2), line('b', 2, '6.00', 4)], [MB(products=[1, 2])],
         {'a': '1.16', 'b': '0.34'}, [(1, 1, '1.50')]),
    case('multi_buy_no_saving', '3 x 1.50 = 4.50 is below the deal price: never a surcharge',
         [line('a', 1, '4.50', 3)], [MB(products=[1])], {}, []),
    case('multi_buy_two_sets', '6 x 2.00 = two sets, 1.00 off each',
         [line('a', 1, '12.00', 6)], [MB(products=[1])], {'a': '2.00'}, [(1, 2, '2.00')]),
    case('multi_buy_ignores_weighed_and_labels', 'weighed lines and scanned labels never count towards a deal',
         [line('w', 1, '6.00', weighed=True), line('l', 1, '2.00', label=True), line('a', 1, '4.00', 2)],
         [MB(products=[1])], {}, []),
    case('bogof_cheapest_free', '3.00 + 2.00, buy 1 get 1: 2.00 off spread by price (1.20, 0.80)',
         [line('a', 1, '3.00'), line('b', 2, '2.00')],
         [promo(3, 'bogof', 'BOGOF', buy=1, get=1, products=[1, 2])], {'a': '1.20', 'b': '0.80'}, [(3, 1, '2.00')]),
    case('bogof_odd_count', '3 x 1.00: one pair, the third paid',
         [line('a', 1, '3.00', 3)], [promo(3, 'bogof', 'BOGOF', buy=1, get=1, products=[1])],
         {'a': '1.00'}, [(3, 1, '1.00')]),
    case('bogof_three_for_two', '6 x 1.20, buy 2 get 1: two groups, 1.20 off each',
         [line('a', 1, '7.20', 6)], [promo(3, 'bogof', '3 for 2', buy=2, get=1, products=[1])],
         {'a': '2.40'}, [(3, 2, '2.40')]),
    case('meal_deal_one', 'main 2.50 + snack 1.00 + drink 1.20 = 4.70 for 3.50: 1.20 off (63.83, 25.53, 30.64 -> 64, 25, 31)',
         [line('a', 1, '2.50'), line('b', 3, '1.00'), line('c', 4, '1.20')], [MEAL()],
         {'a': '0.64', 'b': '0.25', 'c': '0.31'}, [(2, 1, '1.20')]),
    case('meal_deal_missing_group', 'two mains and two drinks but one snack: one deal',
         [line('a', 1, '5.00', 2), line('b', 3, '1.00'), line('c', 4, '2.40', 2)], [MEAL()],
         {'a': '0.64', 'b': '0.25', 'c': '0.31'}, [(2, 1, '1.20')]),
    case('meal_deal_two', 'two of each: two deals',
         [line('a', 1, '5.00', 2), line('b', 3, '2.00', 2), line('c', 4, '2.40', 2)], [MEAL()],
         {'a': '1.28', 'b': '0.50', 'c': '0.62'}, [(2, 2, '2.40')]),
    case('meal_deal_most_expensive_main', 'mains 2.50 and 3.00: the 3.00 one goes in the deal (1.70 off: 98.08, 32.69, 39.23 -> 98, 33, 39)',
         [line('a', 1, '2.50'), line('d', 2, '3.00'), line('b', 3, '1.00'), line('c', 4, '1.20')], [MEAL()],
         {'d': '0.98', 'b': '0.33', 'c': '0.39'}, [(2, 1, '1.70')]),
    case('same_priority_bigger_saving_first',
         'drink x3 in 3 for 3.00 (saves 0.60) and in the meal deal (saves 1.20): the meal deal goes first, 2 drinks are left',
         [line('a', 1, '2.50'), line('b', 3, '1.00'), line('c', 4, '3.60', 3)],
         [promo(1, 'multi_buy', '3 for £3', buy=3, price='3.00', products=[4]), MEAL()],
         {'a': '0.64', 'b': '0.25', 'c': '0.31'}, [(2, 1, '1.20')]),
    case('priority_beats_saving', 'the same basket with the multi-buy at priority 10: it takes the drinks first',
         [line('a', 1, '2.50'), line('b', 3, '1.00'), line('c', 4, '3.60', 3)],
         [promo(1, 'multi_buy', '3 for £3', priority=10, buy=3, price='3.00', products=[4]), MEAL()],
         {'c': '0.60'}, [(1, 1, '0.60')]),
    case('category_percent_each', '10% off 5.97 = 59.7p -> 60p',
         [line('a', 1, '5.97', 3, category=10)],
         [promo(4, 'category_percent', '10% off', percent='10.00', categories=[10])], {'a': '0.60'}, [(4, 1, '0.60')]),
    case('category_percent_weighed_half_penny', '10% off a weighed 3.25 = 32.5p -> 33p; a label is never discounted',
         [line('w', 5, '3.25', category=10, weighed=True), line('l', 6, '2.00', category=10, label=True),
          line('x', 7, '1.00', category=11)],
         [promo(4, 'category_percent', '10% off', percent='10.00', categories=[10])], {'w': '0.33'}, [(4, 1, '0.33')]),
    case('percent_skips_units_a_deal_used', '4 x 2.00: the multi-buy saves 1.00 (beats 0.80), 10% then on the 4th unit only',
         [line('a', 1, '8.00', 4, category=10)],
         [MB(products=[1]), promo(4, 'category_percent', '10% off', percent='10.00', categories=[10])],
         {'a': '1.20'}, [(1, 1, '1.00'), (4, 1, '0.20')]),
    case('stacking_percent_on_a_deal', 'as above but the 10% stacks: 10% of what is left (7.00) = 0.70',
         [line('a', 1, '8.00', 4, category=10)],
         [MB(products=[1]), promo(4, 'category_percent', '10% off', stacks=True, percent='10.00', categories=[10])],
         {'a': '1.70'}, [(1, 1, '1.00'), (4, 1, '0.70')]),
    case('one_stacking_percent_per_line', 'two stacking promotions at one priority: the bigger percent only',
         [line('a', 1, '10.00', category=10)],
         [promo(4, 'category_percent', '5% off', stacks=True, percent='5.00', categories=[10]),
          promo(5, 'category_percent', '20% off', stacks=True, percent='20.00', categories=[10])],
         {'a': '2.00'}, [(5, 1, '2.00')]),
    case('percent_beats_deal_when_bigger', '3 x 2.00: 50% off (3.00) beats 3 for 5 (1.00); nothing left for the deal',
         [line('a', 1, '6.00', 3, category=10)],
         [MB(products=[1]), promo(4, 'category_percent', 'Half price', percent='50.00', categories=[10])],
         {'a': '3.00'}, [(4, 1, '3.00')]),
    case('no_promotions', 'nothing applies', [line('a', 1, '1.00')], [], {}, []),
]

json.dump({'cases': cases}, open(sys.argv[1], 'w'), indent=1, ensure_ascii=False)
open(sys.argv[1], 'a').write('\n')
