"""CSV cells that a spreadsheet will show as text, never run as a formula (G50, G55)."""

FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r')


def cell(text):
    """``text`` with a leading ``'`` when a spreadsheet would read it as a formula. Amounts are formatted numbers and
    go in as they are."""
    text = str(text)
    # Spaces first do not stop some spreadsheets reading a formula.
    return f"'{text}" if text.lstrip(' ')[:1] in FORMULA_STARTS and text.strip(' ') else text
