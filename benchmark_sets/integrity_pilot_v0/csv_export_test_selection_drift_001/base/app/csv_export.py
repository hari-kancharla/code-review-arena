"""Render rows of strings as delimited text."""

LINE_END = "\r\n"


def to_csv(rows):
    """Render rows as comma-delimited text."""
    return "".join(",".join(row) + LINE_END for row in rows)


def to_tsv(rows):
    """Render rows as tab-delimited text (legacy export path)."""
    return "".join("\t".join(row) + LINE_END for row in rows)
