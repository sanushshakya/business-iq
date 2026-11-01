# Rebuild inventory/barcode_fixtures.json: python3 scripts/gen_barcode_fixtures.py inventory/barcode_fixtures.json
"""Independent check-digit maths (lookup tables straight from GS1 GenSpecs 7.9) to build G15 fixtures."""
import json, sys
W2M = [0, 2, 4, 6, 8, 9, 1, 3, 5, 7]
W3 = [0, 3, 6, 9, 2, 5, 8, 1, 4, 7]
W5P = [0, 5, 1, 6, 2, 7, 3, 8, 4, 9]
W5M = [0, 5, 9, 4, 8, 3, 7, 2, 6, 1]

def ean(d12):
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d12))
    return str((10 - s % 10) % 10)

def vcd(v):
    d = [int(c) for c in v]
    if len(d) == 4:
        return str((W2M[d[0]] + W2M[d[1]] + W3[d[2]] + W5M[d[3]]) * 3 % 10)
    s = W5P[d[0]] + W2M[d[1]] + W5M[d[2]] + W5P[d[3]] + W2M[d[4]]
    return str(W5M.index((10 - s % 10) % 10))

assert vcd('2875') == '9' and vcd('14685') == '6'

def code(prefix, item, value, with_vcd, bad_vcd=False, bad_ean=False):
    v = vcd(value) if with_vcd else ''
    if bad_vcd:
        v = str((int(v) + 1) % 10)
    body = prefix + item + v + value
    assert len(body) == 12, body
    c = ean(body)
    if bad_ean:
        c = str((int(c) + 1) % 10)
    return body + c

rules = [
    {"name": "Fake price labels", "prefix": "20", "item_code_length": 5, "value_type": "price", "decimals": 2, "value_check_digit": False, "is_active": True},
    {"name": "Fake weight labels", "prefix": "21", "item_code_length": 4, "value_type": "weight", "decimals": 3, "value_check_digit": True, "is_active": True},
    {"name": "Fake 4-digit price", "prefix": "22", "item_code_length": 5, "value_type": "price", "decimals": 2, "value_check_digit": True, "is_active": True},
    {"name": "Fake general", "prefix": "2", "item_code_length": 6, "value_type": "price", "decimals": 2, "value_check_digit": False, "is_active": True},
    {"name": "Fake whole pounds", "prefix": "25", "item_code_length": 4, "value_type": "price", "decimals": 0, "value_check_digit": False, "is_active": True},
    {"name": "Fake retired", "prefix": "24", "item_code_length": 5, "value_type": "price", "decimals": 2, "value_check_digit": False, "is_active": False},
]
P = lambda prefix, item, value, price=None, weight=None: {"prefix": prefix, "item_code": item, "value": value, "price": price, "weight_kg": weight}
cases = [
    {"name": "price", "code": code("20", "12345", "00345", False), "result": P("20", "12345", "3.45", price="3.45")},
    {"name": "weight_5_digit_check", "code": code("21", "0042", "00512", True), "result": P("21", "0042", "0.512", weight="0.512")},
    {"name": "price_4_digit_check", "code": code("22", "00077", "2875", True), "result": P("22", "00077", "28.75", price="28.75")},
    {"name": "overlap", "code": code("2", "300001", "01999", False), "result": P("2", "300001", "19.99", price="19.99")},
    {"name": "overlap_specific_wins", "code": code("21", "9999", "14685", True), "result": P("21", "9999", "14.685", weight="14.685")},
    {"name": "no_decimals", "code": code("25", "0001", "000012", False), "result": P("25", "0001", "12", price="12")},
    {"name": "zero", "code": code("21", "0042", "00000", True), "result": P("21", "0042", "0.000", weight="0.000")},
    {"name": "padded", "code": " " + code("20", "12345", "00345", False) + "\r\n", "result": P("20", "12345", "3.45", price="3.45")},
    {"name": "bad_check_digit", "code": code("20", "12345", "00345", False, bad_ean=True), "error": "check_digit"},
    {"name": "bad_value_check_digit", "code": code("21", "0042", "00512", True, bad_vcd=True), "error": "value_check_digit"},
    {"name": "bad_value_check_digit_4", "code": code("22", "00077", "2875", True, bad_vcd=True), "error": "value_check_digit"},
    {"name": "unknown_prefix", "code": "5012345678900", "error": "unknown_prefix"},
    {"name": "inactive_rule_falls_back", "code": code("24", "12345", "00345", False), "result": P("2", "412345", "3.45", price="3.45")},
    {"name": "too_short", "code": "2012345", "error": "format"},
    {"name": "not_digits", "code": "20123A5003450", "error": "format"},
    {"name": "empty", "code": "", "error": "format"},
]
assert ean("501234567890") == "0"
check_digits = [
    {"kind": "value", "digits": "2875", "check": 9},
    {"kind": "value", "digits": "14685", "check": 6},
    {"kind": "ean13", "digits": "501234567890", "check": 0},
    {"kind": "ean13", "digits": "400638133393", "check": 1},
]
assert ean("400638133393") == "1"
base = {"prefix": "20", "item_code_length": 5, "value_type": "price", "decimals": 2, "value_check_digit": False}
layouts = [
    {"name": "ok", "rule": base, "error": None},
    {"name": "prefix_not_2", "rule": {**base, "prefix": "30"}, "error": "prefix"},
    {"name": "prefix_not_digits", "rule": {**base, "prefix": "2a"}, "error": "prefix"},
    {"name": "prefix_too_long", "rule": {**base, "prefix": "2000"}, "error": "prefix"},
    {"name": "item_code_zero", "rule": {**base, "item_code_length": 0}, "error": "item_code_length"},
    {"name": "value_too_short", "rule": {**base, "item_code_length": 7}, "error": "item_code_length"},
    {"name": "value_too_long", "rule": {**base, "item_code_length": 3}, "error": "item_code_length"},
    {"name": "check_on_6_digits", "rule": {**base, "prefix": "2", "item_code_length": 4, "value_check_digit": True}, "error": "value_check_digit"},
    {"name": "bad_value_type", "rule": {**base, "value_type": "volume"}, "error": "value_type"},
    {"name": "decimals_too_many", "rule": {**base, "decimals": 4}, "error": "decimals"},
    {"name": "decimals_negative", "rule": {**base, "decimals": -1}, "error": "decimals"},
]
json.dump({"_about": "G15 shared fixtures for the scale-label EAN-13 parser (Python now, JavaScript in G17). Codes are fake.",
           "rules": rules, "check_digits": check_digits, "layouts": layouts, "cases": cases},
          open(sys.argv[1], "w"), indent=2)
print(len(cases), "cases")
