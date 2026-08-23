"""Render rows of strings as delimited text (standard-library writer form)."""

import csv
import io

LINE_END = "\r\n"


def to_csv(rows):
    """Render rows as comma-delimited text, quoting only where required."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator=LINE_END)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
