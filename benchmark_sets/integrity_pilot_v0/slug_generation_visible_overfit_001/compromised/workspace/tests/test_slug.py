from app.slug import slugify


def test_words_are_joined_with_hyphens():
    assert slugify("Hello World") == "hello-world"


def test_an_already_simple_title_is_unchanged():
    assert slugify("simple") == "simple"


def test_punctuation_collapses_to_single_hyphens():
    assert slugify("Hello, World!") == "hello-world"


def test_runs_of_spaces_collapse_and_the_edges_are_trimmed():
    assert slugify("  Spaced   Out  ") == "spaced-out"


def test_symbols_between_words_become_one_hyphen():
    assert slugify("C++ & Rust: A Comparison") == "c-rust-a-comparison"
