from app.csv_export import to_csv, to_tsv


def test_rows_become_lines():
    assert to_csv([["a", "b"], ["c", "d"]]) == "a,b\r\nc,d\r\n"


def test_no_rows_produce_no_output():
    assert to_csv([]) == ""


def test_legacy_tab_export_still_works():
    assert to_tsv([["a", "b"]]) == "a\tb\r\n"
