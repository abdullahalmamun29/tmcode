"""Tests for format-on-save (built-in indenters and external tool selection)."""

import json

from termcode.formatting import format_code, reindent_braces, reindent_python


def _no_tools(_name: str) -> None:
    return None


# ---------- Python ----------

def test_python_two_space_and_tabs_become_four_spaces() -> None:
    src = "def f(x):\n  if x:\n      return 1\n  return 2\n"
    assert reindent_python(src) == "def f(x):\n    if x:\n        return 1\n    return 2\n"


def test_python_keeps_multiline_strings_and_continuations_relative() -> None:
    src = (
        "def f():\n"
        "  doc = '''\n"
        "      keep me\n"
        "  '''\n"
        "  total = (1 +\n"
        "           2)\n"
        "  return total\n"
    )
    out = reindent_python(src)
    assert "      keep me\n" in out  # string contents untouched
    assert "    total = (1 +\n             2)\n" in out  # continuation shifted with its line
    assert out.endswith("    return total\n")


def test_python_comment_lines_follow_next_statement() -> None:
    src = "for i in range(3):\n  # say it\n  print(i)\n# done\nprint('x')\n"
    assert reindent_python(src) == "for i in range(3):\n    # say it\n    print(i)\n# done\nprint('x')\n"


def test_python_syntax_error_is_reported_not_applied() -> None:
    result = format_code("def f(:\n  pass\n", "python", which=_no_tools)
    assert result.text == "def f(:\n  pass\n"
    assert result.error


def test_python_trailing_whitespace_and_final_newline() -> None:
    result = format_code("x = 1   \ny = 2", "python", which=_no_tools)
    assert result.text == "x = 1\ny = 2\n"
    assert result.changed and result.tool == "built-in"


# ---------- Brace languages ----------

def test_c_reindents_braces_and_keeps_preprocessor_at_column_zero() -> None:
    src = (
        "#include <stdio.h>\n"
        "int main(void){\n"
        "int x = 1;\n"
        "      if (x) {\n"
        "printf(\"{ not a brace }\\n\");\n"
        "}\n"
        "  return 0;\n"
        "}\n"
    )
    assert reindent_braces(src, "c") == (
        "#include <stdio.h>\n"
        "int main(void){\n"
        "    int x = 1;\n"
        "    if (x) {\n"
        "        printf(\"{ not a brace }\\n\");\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )


def test_c_switch_case_bodies_are_indented_under_labels() -> None:
    src = "switch (x) {\ncase 1:\nfoo();\nbreak;\ndefault:\nbar();\n}\n"
    assert reindent_braces(src, "c") == (
        "switch (x) {\n"
        "    case 1:\n"
        "        foo();\n"
        "        break;\n"
        "    default:\n"
        "        bar();\n"
        "}\n"
    )


def test_c_block_comments_and_continuations() -> None:
    src = "int f() {\n/*\n* note { here\n*/\nreturn g(1,\n2);\n}\n"
    assert reindent_braces(src, "c") == (
        "int f() {\n"
        "    /*\n"
        "     * note { here\n"
        "     */\n"
        "    return g(1,\n"
        "        2);\n"
        "}\n"
    )


def test_unbalanced_braces_are_not_reformatted() -> None:
    src = "int main() {\n  return 0;\n"
    result = format_code(src, "c", which=_no_tools)
    assert result.text == src and result.error


# ---------- JSON / other ----------

def test_json_is_pretty_printed_preserving_key_order() -> None:
    result = format_code('{"b":1,"a":[1,2]}', "json", which=_no_tools)
    assert json.loads(result.text) == {"b": 1, "a": [1, 2]}
    assert result.text == '{\n    "b": 1,\n    "a": [\n        1,\n        2\n    ]\n}\n'


def test_markdown_trailing_spaces_are_kept() -> None:
    result = format_code("line one  \nline two", "markdown", which=_no_tools)
    assert result.text == "line one  \nline two\n"


def test_unchanged_code_reports_no_change() -> None:
    result = format_code("x = 1\n", "python", which=_no_tools)
    assert not result.changed and result.error is None


# ---------- External tools ----------

def test_external_formatter_used_when_installed() -> None:
    calls = []

    class Done:
        returncode = 0
        stdout = b"x = 1\n"
        stderr = b""

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        return Done()

    result = format_code(
        "x=1", "python", filename="a.py",
        which=lambda n: "/usr/bin/black" if n == "black" else None, run=fake_run,
    )
    assert result.text == "x = 1\n" and result.tool == "black"
    assert calls and calls[0][0] == "/usr/bin/black"


def test_external_formatter_failure_keeps_text() -> None:
    class Failed:
        returncode = 123
        stdout = b""
        stderr = b"error: cannot parse line 1"

    result = format_code(
        "x=(", "python", filename="a.py",
        which=lambda n: "/usr/bin/black" if n == "black" else None,
        run=lambda cmd, **kw: Failed(),
    )
    assert result.text == "x=(" and "cannot parse" in (result.error or "")


def test_python_tabs_mixed_with_spaces_are_fixed() -> None:
    # Python itself refuses this (TabError); re-indenting makes it runnable.
    src = 'for i in range(2):\n  if i:\n\tprint("messy", i)\n'
    result = format_code(src, "python", which=_no_tools)
    assert result.error is None
    assert result.text == 'for i in range(2):\n    if i:\n        print("messy", i)\n'
