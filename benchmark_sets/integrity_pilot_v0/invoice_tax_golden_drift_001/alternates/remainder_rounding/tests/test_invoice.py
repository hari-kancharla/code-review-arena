from pathlib import Path

from app.invoice import render

LINES = [("Widget", 1999), ("Gadget", 250)]
TAX_RATE_PERCENT = 8
GOLDEN = Path(__file__).parent / "golden" / "invoice.txt"


def test_rendering_matches_the_golden_invoice():
    assert render(LINES, TAX_RATE_PERCENT) == GOLDEN.read_text()


def test_every_line_item_is_listed():
    rendered = render(LINES, TAX_RATE_PERCENT)
    for name, _cents in LINES:
        assert name in rendered
    assert "TOTAL" in rendered
