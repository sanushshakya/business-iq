"""G58: the promotion fixtures are shared with the till (fe ``src/utils/promotion_fixtures.json``); both repos pin the
same SHA-256, so changing one copy fails here until the other is updated too."""

import hashlib
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[1] / 'promotion_fixtures.json'
FE_COPY = Path(__file__).resolve().parents[3] / 'fe' / 'src' / 'utils' / 'promotion_fixtures.json'
FIXTURES_SHA256 = '6d7bdc634267230fd9446c919bdb280dadd7ed62d7751a2830e0a773bf0ca171'


def test_fixture_checksum():
    digest = hashlib.sha256(FIXTURES.read_bytes()).hexdigest()
    assert digest == FIXTURES_SHA256, (
        'promotion_fixtures.json changed: copy it to fe/src/utils/ and update both pinned hashes '
        f'(this test and fe src/utils/__tests__/promotions.test.js) to {digest}.')


@pytest.mark.skipif(not FE_COPY.exists(), reason='the fe repo is not checked out next to this one')
def test_matches_the_fe_copy():
    assert FE_COPY.read_bytes() == FIXTURES.read_bytes(), 'copy promotions/promotion_fixtures.json to the fe repo'
