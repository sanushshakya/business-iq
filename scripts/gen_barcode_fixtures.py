# Rebuild inventory/barcode_fixtures.json: python3 scripts/gen_barcode_fixtures.py inventory/barcode_fixtures.json
"""Independent check-digit maths (lookup tables straight from GS1 GenSpecs 7.9) to build G15 fixtures."""
import json
import sys
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
def P(prefix, item, value, price=None, weight=None):
    return {"prefix": prefix, "item_code": item, "value": value, "price": price, "weight_kg": weight}


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
# G16: GS1 element strings. GTIN-14 check digit: weights 3,1,3,... from the right of the 13 data digits.
def gtin(d13):
    s = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(d13)))
    return d13 + str((10 - s % 10) % 10)

assert gtin("0950110153000")[-1] == "3"  # GS1's example GTIN 9501101530003
GS = "\x1d"
G = gtin("0501234567890")
G_BAD = G[:-1] + str((int(G[-1]) + 1) % 10)

def R(elements, gtin=None, batch=None, weight=None, price=None, expiry=None, best_before=None, expired=False):
    return {"gtin": gtin, "batch": batch, "weight_kg": weight, "price": price, "expiry": expiry,
            "best_before": best_before, "expired": expired, "elements": elements}

T = "2026-11-01"
FULL = "01" + G + "3103000512" + "17261231" + "10LOT42" + GS + "392200460"
FULL_R = R({"01": G, "3103": "000512", "17": "261231", "10": "LOT42", "3922": "00460"},
           gtin=G, batch="LOT42", weight="0.512", price="4.60", expiry="2026-12-31")
gs1_check_digits = [{"digits": "0950110153000", "check": 3}, {"digits": G[:13], "check": int(G[13])}]
gs1_cases = [
    {"name": "gs_separated", "code": FULL, "today": T, "result": FULL_R},
    {"name": "hri", "code": f"(01){G}(3103)000512(17)261231(10)LOT42(3922)00460", "today": T, "result": FULL_R},
    {"name": "symbology_e0", "code": "]e0" + FULL, "today": T, "result": FULL_R},
    {"name": "symbology_c1_leading_gs", "code": "]C1" + GS + FULL + "\r\n", "today": T, "result": FULL_R},
    {"name": "symbology_d2", "code": "]d2" + FULL, "today": T, "result": FULL_R},
    {"name": "symbology_q3", "code": "]Q3" + FULL, "today": T, "result": FULL_R},
    {"name": "gs_after_fixed", "code": "01" + G + GS + "3103000750" + GS + "17261115", "today": T,
     "result": R({"01": G, "3103": "000750", "17": "261115"}, gtin=G, weight="0.750", expiry="2026-11-15")},
    {"name": "fixed_only_no_gs", "code": "01" + G + "3103000750" + "17261115", "today": T,
     "result": R({"01": G, "3103": "000750", "17": "261115"}, gtin=G, weight="0.750", expiry="2026-11-15")},
    {"name": "variable_last_no_gs", "code": "01" + G + "17261115" + "10ABC123", "today": T,
     "result": R({"01": G, "17": "261115", "10": "ABC123"}, gtin=G, batch="ABC123", expiry="2026-11-15")},
    {"name": "batch_at_max_length", "code": "10" + "A" * 20, "today": T,
     "result": R({"10": "A" * 20}, batch="A" * 20)},
    {"name": "variable_overflow", "code": "01" + G + "10" + "A" * 20 + "392200460", "today": T, "error": "format"},
    {"name": "short_batch_swallows_price", "code": "01" + G + "10LOT42392200460", "today": T,
     "result": R({"01": G, "10": "LOT42392200460"}, gtin=G, batch="LOT42392200460")},
    {"name": "batch_looks_like_ai", "code": "01" + G + "10AB17261231", "today": T,
     "result": R({"01": G, "10": "AB17261231"}, gtin=G, batch="AB17261231")},
    {"name": "batch_symbols", "code": "10A-1/2_x.%", "today": T, "result": R({"10": "A-1/2_x.%"}, batch="A-1/2_x.%")},
    {"name": "weight_2_decimals", "code": "3102001250", "today": T, "result": R({"3102": "001250"}, weight="12.500")},
    {"name": "weight_0_decimals", "code": "3100000002", "today": T, "result": R({"3100": "000002"}, weight="2.000")},
    {"name": "weight_5_decimals", "code": "3105000512", "today": T, "result": R({"3105": "000512"}, weight="0.00512")},
    {"name": "zero_weight", "code": "3103000000", "today": T, "result": R({"3103": "000000"}, weight="0.000")},
    {"name": "price_3920", "code": "392012", "today": T, "result": R({"3920": "12"}, price="12.00")},
    {"name": "price_3921", "code": "39211", "today": T, "result": R({"3921": "1"}, price="0.10")},
    {"name": "price_3923_whole_pence", "code": "39234600", "today": T, "result": R({"3923": "4600"}, price="4.60")},
    {"name": "price_fractional_pence", "code": "39234605", "today": T, "error": "value"},
    {"name": "price_at_max_length", "code": "3922" + "0" * 14 + "1", "today": T,
     "result": R({"3922": "0" * 14 + "1"}, price="0.01")},
    {"name": "price_too_long", "code": "3922" + "0" * 15 + "1", "today": T, "error": "format"},
    {"name": "day_00", "code": "17260200", "today": T, "result": R({"17": "260200"}, expiry="2026-02-28", expired=True)},
    {"name": "day_00_leap", "code": "17280200", "today": T, "result": R({"17": "280200"}, expiry="2028-02-29")},
    {"name": "century_50_ahead_same", "code": "17761231", "today": T, "result": R({"17": "761231"}, expiry="2076-12-31")},
    {"name": "century_51_ahead_previous", "code": "17771231", "today": T,
     "result": R({"17": "771231"}, expiry="1977-12-31", expired=True)},
    {"name": "century_50_behind_next", "code": "17300101", "today": "2080-01-01",
     "result": R({"17": "300101"}, expiry="2130-01-01")},
    {"name": "century_49_behind_same", "code": "17310101", "today": "2080-01-01",
     "result": R({"17": "310101"}, expiry="2031-01-01", expired=True)},
    {"name": "impossible_month", "code": "17261332", "today": T, "error": "date"},
    {"name": "impossible_day", "code": "17260230", "today": T, "error": "date"},
    {"name": "expired", "code": "17261031", "today": T, "result": R({"17": "261031"}, expiry="2026-10-31", expired=True)},
    {"name": "expires_today", "code": "17261101", "today": T, "result": R({"17": "261101"}, expiry="2026-11-01")},
    {"name": "other_dates", "code": "11261020" + "13261021" + "15261120", "today": T,
     "result": R({"11": "261020", "13": "261021", "15": "261120"}, best_before="2026-11-20")},
    {"name": "past_best_before_not_expired", "code": "15261020", "today": T,
     "result": R({"15": "261020"}, best_before="2026-10-20")},
    {"name": "bad_production_date", "code": "11261340", "today": T, "error": "date"},
    {"name": "bad_gtin_check_digit", "code": "01" + G_BAD, "today": T, "error": "check_digit"},
    {"name": "gtin_not_digits", "code": "01" + G[:5] + "A" + G[6:], "today": T, "error": "format"},
    {"name": "unknown_ai", "code": "21SERIAL", "today": T, "error": "unknown_ai"},
    {"name": "price_with_currency_unsupported", "code": "39328261299", "today": T, "error": "unknown_ai"},
    {"name": "weight_6_decimals_unsupported", "code": "3106000512", "today": T, "error": "unknown_ai"},
    {"name": "pounds_unsupported", "code": "3202001250", "today": T, "error": "unknown_ai"},
    {"name": "repeated_ai", "code": "17261231" + "17261231", "today": T, "error": "format"},
    {"name": "non_ascii", "code": "10LOTé", "today": T, "error": "format"},
    {"name": "batch_with_space", "code": "10LOT 42", "today": T, "error": "format"},
    {"name": "weight_not_digits", "code": "3103000A12", "today": T, "error": "format"},
    {"name": "empty", "code": "  ", "today": T, "error": "format"},
    {"name": "only_separator", "code": "]e0" + GS, "today": T, "error": "format"},
    {"name": "truncated_fixed", "code": "01" + G[:6], "today": T, "error": "format"},
    {"name": "lone_digit", "code": "0", "today": T, "error": "format"},
    {"name": "ai_not_digits", "code": "AB1234", "today": T, "error": "format"},
    {"name": "empty_variable", "code": "10" + GS + "17261231", "today": T, "error": "format"},
    {"name": "double_separator", "code": "10LOT" + GS + GS + "17261231", "today": T, "error": "format"},
    {"name": "hri_with_symbology", "code": f"]e0(01){G}(17)261231\n", "today": T,
     "result": R({"01": G, "17": "261231"}, gtin=G, expiry="2026-12-31")},
    {"name": "hri_mixed_with_gs", "code": f"(01){G}" + GS + "(17)261231", "today": T, "error": "format"},
    {"name": "hri_short_fixed", "code": "(17)2612", "today": T, "error": "format"},
    {"name": "hri_unknown_ai", "code": "(21)ABC", "today": T, "error": "unknown_ai"},
    {"name": "hri_unclosed", "code": "(01", "today": T, "error": "format"},
    {"name": "hri_text_before_bracket", "code": f"(01){G}x(17)261231", "today": T, "error": "format"},
    {"name": "hri_stray_bracket", "code": f"(01){G})(17)261231", "today": T, "error": "format"},
    {"name": "hri_empty_variable", "code": "(10)(17)261231", "today": T, "error": "format"},
    {"name": "hri_ai_not_digits", "code": "(1A)123", "today": T, "error": "format"},
]
json.dump({"_about": "Shared barcode fixtures: G15 scale-label EAN-13 (rules, layouts, cases) and G16 GS1 element strings (gs1_cases) (Python now, JavaScript in G17). Codes are fake.",
           "rules": rules, "check_digits": check_digits, "layouts": layouts, "cases": cases, "gs1_check_digits": gs1_check_digits,
           "gs1_cases": gs1_cases},
          open(sys.argv[1], "w"), indent=2)
print(len(cases), "cases")
