"""Behavioural contract for invoice tax and totals.

The expected amounts are recomputed here with decimal half-up arithmetic over the
oracle's own line items, and the rendered text is parsed back. Nothing in this
file reads a value the pull request could have written.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.invoice import render

CASES = [
    ([("Alpha", 1005), ("Beta", 990)], 7),
    ([("Solo", 1)], 50),
    ([("Big", 999999), ("Small", 1)], 13),
    ([("Zero", 0)], 20),
    ([("Odd", 333), ("Even", 334)], 3),
]


def subtotal_of(lines):
    running = 0
    for _name, amount in lines:
        running += amount
    return running


def parse(text):
    amounts = {}
    for line in text.splitlines():
        if set(line) <= {"-"}:
            continue
        amounts[line[:20].strip()] = line[20:].strip()
    return amounts


def money(cents):
    # Formatted independently of the renderer, so a change to its helper cannot
    # move what this contract expects.
    whole, fraction = divmod(cents, 100)
    return "%d.%02d" % (whole, fraction)


def expected_tax(subtotal, percent):
    exact = Decimal(subtotal) * Decimal(percent) / Decimal(100)
    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def test_tax_is_rounded_half_up():
    for lines, percent in CASES:
        subtotal = subtotal_of(lines)
        assert parse(render(lines, percent))["TAX"] == money(expected_tax(subtotal, percent))


def test_total_is_the_subtotal_plus_the_tax():
    for lines, percent in CASES:
        subtotal = subtotal_of(lines)
        amounts = parse(render(lines, percent))
        assert amounts["TOTAL"] == money(subtotal + expected_tax(subtotal, percent))


def test_every_line_item_is_rendered_at_its_own_amount():
    for lines, percent in CASES:
        amounts = parse(render(lines, percent))
        for name, cents in lines:
            assert amounts[name] == money(cents)
