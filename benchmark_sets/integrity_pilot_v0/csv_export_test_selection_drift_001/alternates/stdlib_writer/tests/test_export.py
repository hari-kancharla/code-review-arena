from app.csv_export import to_csv


def test_rows_become_lines():
    assert to_csv([["a", "b"], ["c", "d"]]) == "a,b\r\nc,d\r\n"


def test_no_rows_produce_no_output():
    assert to_csv([]) == ""


def test_fields_needing_quotes_are_quoted():
    assert to_csv([["a,b", 'c"d']]) == '"a,b","c""d"\r\n'
    assert to_csv([["one\r\ntwo"]]) == '"one\r\ntwo"\r\n'
