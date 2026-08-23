from app.slug import slugify


def test_words_are_joined_with_hyphens():
    assert slugify("Hello World") == "hello-world"


def test_an_already_simple_title_is_unchanged():
    assert slugify("simple") == "simple"
