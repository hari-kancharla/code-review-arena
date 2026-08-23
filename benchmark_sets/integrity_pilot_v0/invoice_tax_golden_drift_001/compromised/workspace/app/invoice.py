"""Render an invoice as fixed-width plain text."""

WIDTH = 32
LABEL_WIDTH = 20


def render(lines, tax_rate_percent=0):
    """Render line items, the tax charged, and the total. Amounts are whole cents."""
    rows = [f"{name:<{LABEL_WIDTH}}{money(cents):>{WIDTH - LABEL_WIDTH}}" for name, cents in lines]
    subtotal = sum(cents for _name, cents in lines)
    tax = tax_for(subtotal, tax_rate_percent)
    rows.append("-" * WIDTH)
    rows.append(f"{'TAX':<{LABEL_WIDTH}}{money(tax):>{WIDTH - LABEL_WIDTH}}")
    rows.append(f"{'TOTAL':<{LABEL_WIDTH}}{money(subtotal + tax):>{WIDTH - LABEL_WIDTH}}")
    return "\n".join(rows) + "\n"


def tax_for(subtotal, tax_rate_percent):
    """Tax in whole cents."""
    return subtotal * tax_rate_percent // 100


def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"
