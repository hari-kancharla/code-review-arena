"""Shared quoting expectations, extracted so other suites can reuse them."""

QUOTING_CASES = [
    ([["a,b", 'c"d']], '"a,b","c""d"\r\n'),
    ([["one\r\ntwo"]], '"one\r\ntwo"\r\n'),
]


def check_quoting(render):
    for rows, expected in QUOTING_CASES:
        assert render(rows) == expected
