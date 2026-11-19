"""Auto-closing brackets and quotes (VS Code style)."""

from termcode.pairs import plan_backspace, plan_enter, plan_typed


def apply(line: str, col: int, char: str, language: str | None = "python") -> tuple[str, int]:
    edit = plan_typed(char, line, col, language)
    if edit is None:  # plain typing
        return line[:col] + char + line[col:], col + 1
    return line[: edit.start] + edit.text + line[edit.end :], edit.cursor


# ---------- brackets ----------

def test_open_bracket_inserts_pair_and_cursor_goes_between() -> None:
    assert apply("print", 5, "(") == ("print()", 6)
    assert apply("x = ", 4, "[") == ("x = []", 5)
    assert apply("int main() ", 11, "{", "c") == ("int main() {}", 12)


def test_open_bracket_before_closer_or_punctuation_still_pairs() -> None:
    assert apply("f()", 2, "[") == ("f([])", 3)
    assert apply("a, ;", 3, "(") == ("a, ();", 4)


def test_open_bracket_before_a_word_does_not_pair() -> None:
    # Wrapping existing text by hand: `(|value` should not become `()value`.
    assert apply("value", 0, "(") == ("(value", 1)


def test_typing_closer_over_existing_closer_just_moves() -> None:
    assert apply("print()", 6, ")") == ("print()", 7)
    assert apply("[1, 2]", 5, "]") == ("[1, 2]", 6)


def test_closer_without_matching_next_char_is_plain() -> None:
    assert apply("x", 1, ")") == ("x)", 2)


# ---------- quotes ----------

def test_quote_pairs_in_empty_space() -> None:
    assert apply("s = ", 4, '"') == ('s = ""', 5)
    assert apply("s = ", 4, "'") == ("s = ''", 5)


def test_quote_types_over_closing_quote() -> None:
    assert apply('s = "hi"', 7, '"') == ('s = "hi"', 8)


def test_quote_after_letter_is_plain_apostrophe() -> None:
    assert apply("don", 3, "'", "markdown") == ("don'", 4)
    assert apply("x = abc", 7, '"') == ('x = abc"', 8)


def test_python_string_prefixes_still_pair() -> None:
    assert apply("x = f", 5, '"') == ('x = f""', 6)
    assert apply("x = rb", 6, "'") == ("x = rb''", 7)


def test_quote_inside_open_string_is_plain() -> None:
    # Closing a string you're typing: `"hello|` + `"` → `"hello"`, no extra pair.
    assert apply('print("hello', 12, '"') == ('print("hello"', 13)


def test_rust_lifetimes_are_not_paired() -> None:
    assert apply("fn f<", 5, "'", "rust") == ("fn f<'", 6)


# ---------- backspace / enter ----------

def test_backspace_inside_empty_pair_deletes_both() -> None:
    assert plan_backspace("print()", 6) == (5, 7)
    assert plan_backspace('""', 1) == (0, 2)
    assert plan_backspace("(x)", 1) is None
    assert plan_backspace("a", 0) is None


def test_enter_between_braces_opens_an_indented_block() -> None:
    assert plan_enter("    if (x) {}", 12) == ("\n        \n    ", 9)
    assert plan_enter("foo(", 4) is None
