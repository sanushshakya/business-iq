"""UK VAT registration numbers, for our company (G43) and trade customers (G38): stored without spaces."""

import re

# GB (or XI for Northern Ireland) + 9 digits, or 12 for a group/branch; GBGD/GBHA + 3 for government bodies and
# health authorities.
VAT_NUMBER = re.compile(r'(GB|XI)(\d{9}|\d{12})|GB(GD|HA)\d{3}', re.ASCII)


def normalise_vat_number(value):
    return re.sub(r'[\s.-]', '', value or '').upper()


def vat_number_problem(number):
    if number and not VAT_NUMBER.fullmatch(number):
        return 'Not a UK VAT number (GB or XI and 9 or 12 digits).'
    return None
