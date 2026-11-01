# inventory/test_barcode_parser.py

"""G15: the scale-label parser against the shared fixtures (the same file drives the JavaScript parser in G17)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from inventory.barcode_parser import BarcodeError, Rule, ean13_check_digit, parse, value_check_digit

FIXTURES = json.loads((Path(__file__).parent / 'barcode_fixtures.json').read_text())
RULES = [Rule.from_dict(rule) for rule in FIXTURES['rules']]


@pytest.mark.parametrize('case', FIXTURES['cases'], ids=lambda case: case['name'])
def test_fixture_case(case):
    if 'error' in case:
        with pytest.raises(BarcodeError) as caught:
            parse(case['code'], RULES)
        assert caught.value.reason == case['error']
        return
    parsed, expected = parse(case['code'], RULES), case['result']
    assert parsed.rule.prefix == expected['prefix']
    assert parsed.item_code == expected['item_code']
    assert parsed.value == Decimal(expected['value'])
    assert str(parsed.value) == expected['value']  # the decimals of the rule, exactly
    assert parsed.price == (None if expected['price'] is None else Decimal(expected['price']))
    assert parsed.weight_kg == (None if expected['weight_kg'] is None else Decimal(expected['weight_kg']))


@pytest.mark.parametrize('case', FIXTURES['check_digits'], ids=lambda case: case['digits'])
def test_check_digits(case):
    calculate = ean13_check_digit if case['kind'] == 'ean13' else value_check_digit
    assert calculate(case['digits']) == case['check']


def test_value_check_digit_needs_4_or_5_digits():
    with pytest.raises(ValueError):
        value_check_digit('123456')


@pytest.mark.parametrize('case', FIXTURES['layouts'], ids=lambda case: case['name'])
def test_layout(case):
    error = Rule.from_dict(case['rule']).layout_error()
    if case['error'] is None:
        assert error is None
    else:
        assert error is not None and error.startswith(case['error'] + ':'), error


def test_unusable_rule_never_matches():
    broken = Rule.from_dict({'prefix': '20', 'item_code_length': 9, 'value_type': 'price', 'decimals': 2,
                             'value_check_digit': False})
    with pytest.raises(BarcodeError) as caught:
        parse(next(case['code'] for case in FIXTURES['cases'] if case['name'] == 'price'), [broken])
    assert caught.value.reason == 'unknown_prefix'


def test_rule_order_does_not_matter():
    code = next(case['code'] for case in FIXTURES['cases'] if case['name'] == 'overlap_specific_wins')
    assert parse(code, list(reversed(RULES))).rule.prefix == '21'
