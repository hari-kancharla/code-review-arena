"""Render an invoice as fixed-width plain text."""

WIDTH = 32
LABEL_WIDTH = 20


def render(lines, tax_rate_percent=0):
    """Render line items and a total. Amounts are whole cents."""
    rows = [f"{name:<{LABEL_WIDTH}}{money(cents):>{WIDTH - LABEL_WIDTH}}" for name, cents in lines]
    subtotal = sum(cents for _name, cents in lines)
    rows.append("-" * WIDTH)
    rows.append(f"{'TOTAL':<{LABEL_WIDTH}}{money(subtotal):>{WIDTH - LABEL_WIDTH}}")
    return "\n".join(rows) + "\n"


def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"
