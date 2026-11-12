from django.test import SimpleTestCase

from common.csv_safe import cell


class CellTests(SimpleTestCase):
    def test_formulas_are_text_even_behind_spaces(self):
        for text in ('=1+1', '+1', '-1', '@SUM(A1)', '\t=1', ' =HYPERLINK("x")', '  @x'):
            with self.subTest(text=text):
                self.assertEqual(cell(text), f"'{text}")
        for text in ('Fake Bistro', 'INV-000001', '', '12.00'):
            self.assertEqual(cell(text), text)
