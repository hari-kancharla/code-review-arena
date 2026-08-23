"""Render rows of strings as delimited text."""

LINE_END = "\r\n"


def to_csv(rows):
    """Render rows as comma-delimited text, quoting only where required."""
    return "".join(",".join(_field(value) for value in row) + LINE_END for row in rows)


def _field(value):
    if "," in value:
        return '"' + value.replace('"', '""') + '"'
    return value
