# inventory/test_gs1_parser.py

"""G16: the GS1 element-string parser against the shared fixtures (the same file drives the JavaScript port in G17)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from inventory.gs1_parser import GS1Error, gtin_check_digit, parse_gs1

FIXTURES = json.loads((Path(__file__).parent / 'barcode_fixtures.json').read_text())


def as_text(value):
    return None if value is None else str(value) if not isinstance(value, date) else value.isoformat()


@pytest.mark.parametrize('case', FIXTURES['gs1_cases'], ids=lambda case: case['name'])
def test_fixture_case(case):
    today = date.fromisoformat(case['today'])
    if 'error' in case:
        with pytest.raises(GS1Error) as caught:
            parse_gs1(case['code'], today)
        assert caught.value.reason == case['error']
        return
    parsed, expected = parse_gs1(case['code'], today), case['result']
    for field in ('gtin', 'batch', 'weight_kg', 'price', 'expiry', 'best_before'):
        assert as_text(getattr(parsed, field)) == expected[field], field
    assert parsed.expired is expected['expired']
    assert parsed.elements == expected['elements']
    for field in ('weight_kg', 'price'):
        assert getattr(parsed, field) is None or isinstance(getattr(parsed, field), Decimal)


@pytest.mark.parametrize('case', FIXTURES['gs1_check_digits'], ids=lambda case: case['digits'])
def test_gtin_check_digit(case):
    assert gtin_check_digit(case['digits']) == case['check']


def test_parsed_is_frozen():
    parsed = parse_gs1('17261231', date(2026, 11, 1))
    with pytest.raises(AttributeError):
        parsed.expired = True
