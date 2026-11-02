# inventory/test_barcode_fixtures_checksum.py

"""G17: the barcode fixtures are shared with the till (fe ``src/utils/barcodes/barcode_fixtures.json``); both repos pin
the same SHA-256, so changing one copy fails here until the other is updated too."""

import hashlib
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / 'barcode_fixtures.json'
FE_COPY = Path(__file__).resolve().parents[2] / 'fe' / 'src' / 'utils' / 'barcodes' / 'barcode_fixtures.json'
FIXTURES_SHA256 = 'e7d60e39394af71927a36d9987579d091481cbee7a0e2eb223f241fa3b095f98'


def test_fixture_checksum():
    digest = hashlib.sha256(FIXTURES.read_bytes()).hexdigest()
    assert digest == FIXTURES_SHA256, (
        'barcode_fixtures.json changed: copy it to fe/src/utils/barcodes/ and update both pinned hashes '
        f'(this test and fe src/utils/barcodes/__tests__/fixtures.test.js) to {digest}.')


@pytest.mark.skipif(not FE_COPY.exists(), reason='the fe repo is not checked out next to this one')
def test_matches_the_fe_copy():
    assert FE_COPY.read_bytes() == FIXTURES.read_bytes(), 'copy inventory/barcode_fixtures.json to the fe repo'
