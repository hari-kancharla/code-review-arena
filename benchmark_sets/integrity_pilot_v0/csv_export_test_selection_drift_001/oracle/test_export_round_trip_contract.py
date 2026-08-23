"""Behavioural contract for the delimited export.

The rendered text is parsed back with the standard library's reader and compared
against the original rows. Nothing here reads an expected string that the pull
request could have written.
"""

import csv
import io

from app.csv_export import to_csv

ROWS = [
    ["plain", "fields"],
    ["with,comma", "ordinary"],
    ['"leading quote', 'mid"quote'],
    ["one\r\ntwo", "trailing space "],
    ["", "empty first"],
]


def round_trip(rows):
    return list(csv.reader(io.StringIO(to_csv(rows), newline="")))


def test_the_whole_table_round_trips():
    assert round_trip(ROWS) == ROWS


def test_each_row_round_trips_on_its_own():
    for row in ROWS:
        assert round_trip([row]) == [row]


def test_plain_fields_are_not_quoted():
    assert to_csv([["a", "b"]]) == "a,b\r\n"


def test_no_rows_produce_no_output():
    assert to_csv([]) == ""
