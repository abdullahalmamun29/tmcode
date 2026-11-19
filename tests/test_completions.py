"""Unit tests for offline code completion and auto-indentation helpers."""

from termcode.completions import (
    compute_closing_brace_dedent,
    compute_enter_indent,
    extract_prefix,
    get_completions,
)


def test_extract_prefix() -> None:
    assert extract_prefix("    pri", 7) == "pri"
    assert extract_prefix("std::co", 7) == "std::co"
    assert extract_prefix("x = my_var", 10) == "my_var"
    assert extract_prefix("print(", 6) == ""
    assert extract_prefix("a", 1) == "a"


def test_c_cpp_python_and_js_suggestions() -> None:
    c_items = get_completions("print", "c", "")
    assert c_items[0] == "printf"

    cpp_items = get_completions("std::c", "cpp", "")
    assert "std::cout" in cpp_items and "std::cin" in cpp_items

    py_items = get_completions("enu", "python", "")
    assert "enumerate" in py_items

    js_items = get_completions("cons", "javascript", "")
    assert "console.log" in js_items or "const" in js_items


def test_suggestions_include_buffer_identifiers_and_skip_exact_match() -> None:
    code = "def calculate_total(items):\n    calc_val = 0\n    return calc"
    items = get_completions("calc", "python", code)
    assert "calculate_total" in items
    assert "calc_val" in items
    assert "calc" not in items  # Do not suggest the exact already-typed token


def test_auto_indent_python_brace_and_plain() -> None:
    assert compute_enter_indent("def foo():", "python") == "    "
    assert compute_enter_indent("    if x > 0:", "python") == "        "
    assert compute_enter_indent("    x = 1", "python") == "    "
    assert compute_enter_indent("int main() {", "c") == "    "
    assert compute_enter_indent("    if (a) {", "cpp") == "        "
    assert compute_enter_indent("  <div>", "html") == "      "
    assert compute_enter_indent("  plain text", None) == "  "


def test_closing_brace_dedent() -> None:
    assert compute_closing_brace_dedent("        ") == 4
    assert compute_closing_brace_dedent("    ") == 4
    assert compute_closing_brace_dedent("  ") == 2
    assert compute_closing_brace_dedent("    x = 1") == 0
    assert compute_closing_brace_dedent("") == 0
