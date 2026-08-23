from app.preview import clip, escape, preview


def test_a_long_text_is_cut_to_the_limit():
    assert preview("abcdef", 3) == "abc"


def test_a_short_text_is_unchanged():
    assert preview("hi", 10) == "hi"


def test_escape_replaces_the_markup_characters():
    assert escape("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_escape_leaves_ordinary_text_alone():
    assert escape("plain text 123") == "plain text 123"


def test_clip_returns_at_most_the_limit():
    assert clip("abcdef", 3) == "abc"
    assert clip("ab", 5) == "ab"


def test_a_plain_preview_is_unchanged():
    assert preview("hello", 10) == "hello"
