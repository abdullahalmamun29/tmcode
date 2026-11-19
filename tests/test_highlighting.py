"""Tests for C/C++ tree-sitter syntax highlighting registration."""

from textual.widgets import TextArea

from termcode.highlighting import register_extra_languages


def test_register_c_and_cpp_languages_on_text_area() -> None:
    ta = TextArea()
    register_extra_languages(ta)
    assert "c" in ta.available_languages
    assert "cpp" in ta.available_languages

    # Applying C and C++ code must not raise query/grammar errors
    ta.language = "c"
    ta.text = '#include <stdio.h>\nint main() { printf("hi\\n"); return 0; }\n'

    ta.language = "cpp"
    ta.text = '#include <iostream>\nint main() { std::cout << "hi\\n"; return 0; }\n'
