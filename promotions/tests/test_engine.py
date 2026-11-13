"""G58: the promotion engine against the shared fixture table (the till runs the same cases in JavaScript)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from promotions.engine import EngineLine, EnginePromotion, apply

CASES = json.loads((Path(__file__).parents[1] / 'promotion_fixtures.json').read_text())['cases']


def _line(row):
    return EngineLine(key=row['key'], product=row['product'], category=row['category'], quantity=row['quantity'],
                      gross=Decimal(row['gross']), weighed=row['weighed'], label=row['label'])


def _promotion(row):
    money = lambda value: None if value is None else Decimal(value)  # noqa: E731
    return EnginePromotion(
        id=row['id'], name=row['name'], kind=row['kind'], priority=row['priority'], stacks=row['stacks'],
        buy_quantity=row['buy_quantity'], get_quantity=row['get_quantity'], deal_price=money(row['deal_price']),
        percent=money(row['percent']), products=frozenset(row['products']), categories=frozenset(row['categories']),
        groups=tuple(frozenset(group) for group in row['groups']))


@pytest.mark.parametrize('case', CASES, ids=[case['name'] for case in CASES])
def test_fixture(case):
    result = apply([_line(row) for row in case['lines']], [_promotion(row) for row in case['promotions']])
    assert {key: str(value) for key, value in result.discounts.items()} == case['expected']['discounts']
    assert [{'promotion': a.promotion, 'times': a.times, 'saving': str(a.saving)} for a in result.applied] == \
        case['expected']['applied']


def test_names_and_kinds_are_reported():
    case = next(c for c in CASES if c['name'] == 'meal_deal_one')
    result = apply([_line(row) for row in case['lines']], [_promotion(row) for row in case['promotions']])
    assert (result.applied[0].name, result.applied[0].kind) == ('Meal deal', 'meal_deal')


def test_a_discount_never_exceeds_its_line():
    lines = [EngineLine('a', 1, None, 3, Decimal('0.03'))]
    deal = EnginePromotion(1, '3 for 1p', 'multi_buy', 0, False, 3, None, Decimal('0.01'), None, frozenset({1}),
                           frozenset(), ())
    result = apply(lines, [deal])
    assert result.discounts == {'a': Decimal('0.02')}
    free = EngineLine('z', 1, None, 2, Decimal('0.00'))
    assert apply([free], [deal]).discounts == {}  # nothing to save on free items
