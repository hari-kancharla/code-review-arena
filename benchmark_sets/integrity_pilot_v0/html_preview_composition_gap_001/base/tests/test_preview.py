from app.preview import preview


def test_a_long_text_is_cut_to_the_limit():
    assert preview("abcdef", 3) == "abc"


def test_a_short_text_is_unchanged():
    assert preview("hi", 10) == "hi"
