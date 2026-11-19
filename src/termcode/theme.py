"""TermCode color tokens and the matching editor (TextArea) syntax theme.

The same hex values are declared as `$variables` at the top of styles.tcss.
Keep the two in sync when changing the palette.
"""

from __future__ import annotations

from rich.style import Style
from textual.widgets.text_area import TextAreaTheme

# Surfaces: one blue-slate family, darkest at the edges, lightest where you type.
BG = "#14171c"        # app chrome: sidebar, tab bar, status bar, output
SURFACE = "#191d23"   # editor canvas
RAISED = "#20252d"    # cursor line, hovered rows, inputs
LINE = "#2a303a"      # hairlines between regions
# Text
TEXT = "#c7ccd4"
MUTED = "#6c7584"
FAINT = "#465061"
# One accent for focus and the active thing
ACCENT = "#8ea2ff"
# Run states, used only by the Output panel
OK = "#7ccf9e"
FAIL = "#f07a7d"
BUSY = "#e7bf72"

_SYNTAX = {
    "comment": Style(color="#5f6878", italic=True),
    "string": Style(color="#a9cc8c"),
    "string.documentation": Style(color="#7f9a6b", italic=True),
    "keyword": Style(color="#b6a0f2"),
    "conditional": Style(color="#b6a0f2"),
    "repeat": Style(color="#b6a0f2"),
    "exception": Style(color="#b6a0f2"),
    "include": Style(color="#b6a0f2"),
    "keyword.function": Style(color="#b6a0f2"),
    "keyword.return": Style(color="#b6a0f2"),
    "keyword.operator": Style(color="#b6a0f2"),
    "operator": Style(color="#8d97a8"),
    "function": Style(color="#7fb6ec"),
    "function.call": Style(color="#7fb6ec"),
    "method": Style(color="#7fb6ec"),
    "method.call": Style(color="#7fb6ec"),
    "constructor": Style(color="#e5bd7f"),
    "class": Style(color="#e5bd7f"),
    "type": Style(color="#e5bd7f"),
    "type.class": Style(color="#e5bd7f"),
    "type.builtin": Style(color="#79c9c1"),
    "number": Style(color="#ec9f7f"),
    "float": Style(color="#ec9f7f"),
    "boolean": Style(color="#ec9f7f"),
    "constant.builtin": Style(color="#ec9f7f"),
    "json.null": Style(color="#ec9f7f"),
    "tag": Style(color="#e5bd7f"),
    "yaml.field": Style(color="#7fb6ec"),
    "json.label": Style(color="#7fb6ec"),
    "toml.type": Style(color="#7fb6ec"),
    "toml.datetime": Style(color="#ec9f7f"),
    "css.property": Style(color="#7fb6ec"),
    "heading": Style(color=TEXT, bold=True),
    "heading.marker": Style(color=MUTED),
    "bold": Style(bold=True),
    "italic": Style(italic=True),
    "strikethrough": Style(strike=True),
    "link.label": Style(color="#b6a0f2"),
    "link.uri": Style(color="#7fb6ec", underline=True),
    "list.marker": Style(color=MUTED),
    "inline_code": Style(color="#a9cc8c"),
    "punctuation.bracket": Style(color="#8d97a8"),
    "punctuation.delimiter": Style(color="#8d97a8"),
    "punctuation.special": Style(color="#8d97a8"),
}

EDITOR_THEME_NAME = "termcode"

EDITOR_THEME = TextAreaTheme(
    name=EDITOR_THEME_NAME,
    base_style=Style(color=TEXT, bgcolor=SURFACE),
    gutter_style=Style(color=FAINT, bgcolor=SURFACE),
    cursor_style=Style(color=SURFACE, bgcolor=ACCENT),
    cursor_line_style=Style(bgcolor=RAISED),
    cursor_line_gutter_style=Style(color=TEXT, bgcolor=RAISED),
    bracket_matching_style=Style(bgcolor="#323a48", bold=True),
    selection_style=Style(bgcolor="#2f3b5c"),
    syntax_styles=_SYNTAX,
)

OUTPUT_THEME_NAME = "termcode-output"

OUTPUT_THEME = TextAreaTheme(
    name=OUTPUT_THEME_NAME,
    base_style=Style(color=TEXT, bgcolor=BG),
    gutter_style=Style(color=FAINT, bgcolor=BG),
    cursor_style=Style(color=BG, bgcolor=MUTED),
    cursor_line_style=Style(bgcolor=BG),
    selection_style=Style(bgcolor="#2f3b5c"),
    syntax_styles={},
)
