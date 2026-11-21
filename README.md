# TmCode

Lightweight, cross-platform VS Code-style terminal code editor for **any language**: open, highlight and run code right in your terminal.

## Features

- Launch right from any terminal (`tmcode` from `~` opens the Home folder picker; `tmcode` inside any project folder opens it directly)
- VS Code Dark Modern 3-pane layout: Explorer with `+F`/`+D` buttons, multi-tab + side-by-side split editor (`F12`), and draggable/resizable Terminal panel
- Syntax colors for **hundreds of languages**: precise tree-sitter highlighting for Python, C, C++, JS/TS, HTML, CSS, JSON, Java, Go, Rust, Bash, SQL, Markdown, YAML, TOML, XML and Regex, plus Pygments for everything else (Kotlin, Swift, C#, Haskell, Elixir, Zig, Dart, R, Julia, Dockerfile, ...)
- Lightweight offline code suggestions (language builtins + open-file words) and automatic indentation while typing
- Auto-closing brackets and quotes like VS Code: `(` `[` `{` `"` `'` insert their partner, typing the closer steps over it, Backspace removes an empty pair, Enter inside `{}` opens an indented block, and typing a bracket with text selected wraps it
- Run any language with `F5` (live keyboard input) or `Shift+F5` (full terminal): Python, C, C++, JavaScript, TypeScript, Bash, Go, Rust, Java, Ruby, PHP, Perl, Lua, Kotlin, Swift, Dart, C#, F#, Haskell, Elixir, Erlang, R, Julia, Scala, Zig, Nim, Crystal, D, Fortran, Pascal, Ada, COBOL, OCaml, Clojure, Groovy, PowerShell, Zsh, Fish, Tcl, Racket, Scheme, Common Lisp, Raku, AWK, Octave, CoffeeScript, Mojo, Odin, Vala and SQL (SQLite). Any script with a `#!` line runs too, and you can add your own commands (see [Run any language](#run-any-language))
- Update prompt on start, like Codex: **Update now**, **Skip**, or **Skip until next version** (checks at most once a day; turn off with `TMCODE_NO_UPDATE_CHECK=1`). `tmcode --update` updates any time
- Real OS clipboard integration (Linux X11/Wayland, macOS, Windows), `Ctrl+A` select-all, and VS Code-style `Ctrl+C` (copies selection or current line)
- Searchable `/` command menu (`Ctrl+K` anywhere, or `/` outside text inputs) with a shortcut for every action

## Installation

One command, no Python needed:

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/abdullahalmamun29/tmcode/main/install.sh | sh
```

**Windows** (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/abdullahalmamun29/tmcode/main/install.ps1 | iex"
```

Then open a new terminal and run `tmcode` inside a project folder.

The installer downloads the ready-made TmCode for your system from [Releases](https://github.com/abdullahalmamun29/tmcode/releases) (and checks its SHA-256). If there is none for your system, it installs TmCode with [uv](https://docs.astral.sh/uv/) instead, which fetches Python by itself.

- **Update:** run the same command again.
- **Uninstall (macOS / Linux):** `rm -rf ~/.local/share/tmcode ~/.local/bin/tmcode ~/.local/bin/termcode`
- **Uninstall (Windows):** delete `%LOCALAPPDATA%\tmcode` and remove it from your user PATH.

### Already have Python? Install from PyPI

```bash
pipx install tmcode        # or: uv tool install tmcode
```

### Good to know
- To **run** code you need the language itself installed: `python3`, `gcc`/`g++` for C/C++, `node`, `go`, `rustc`, `java`, etc.
- **Format on save** works out of the box with a built-in re-indenter. If `ruff`/`black`, `clang-format`, `prettier`, `gofmt` or `rustfmt` are installed, TmCode uses them automatically.
- On Windows use **Windows Terminal**. The resize pointer (↔ ↕ on panel edges) shows in kitty, WezTerm, Ghostty, foot and xterm; other terminals show the highlighted grip instead.

### From source (for development)
```bash
git clone https://github.com/abdullahalmamun29/tmcode.git
cd tmcode
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

### Making a release (maintainers)
Bump `version` in `pyproject.toml`, commit, then push a tag:
```bash
git tag v0.2.1 && git push origin v0.2.1
```
GitHub Actions runs the tests, builds TmCode for Linux, macOS (Intel + Apple Silicon) and Windows, and publishes a Release with checksums. See `.github/workflows/release.yml` to also publish to PyPI automatically.

## Run any language

`F5` picks the right command from the file extension. You need that language installed (for example `kotlinc` for `.kt`); if it's missing, TmCode tells you what to install.

For anything else:

- **`#!` line:** a first line like `#!/usr/bin/env ruby` tells TmCode how to run the file, even with no extension.
- **Your own commands:** create `run-commands.json` in TmCode's settings folder (`~/.config/termcode/` on Linux, `~/Library/Application Support/termcode/` on macOS, `%LOCALAPPDATA%\termcode\termcode\` on Windows). Your commands also override the built-in ones:

```json
{
  ".foo": "foo-run {file}",
  ".py": "python3 -X dev {file}",
  ".bar": { "compile": "barc {file} -o {out}", "run": "{out}" }
}
```

Placeholders: `{file}` the file, `{out}` a temporary program path for compiled languages, `{dir}` the file's folder, `{stem}` the file name without extension.

## Usage

```bash
tmcode                    # From ~ opens the Home tab (folder selector & recent folders); from any project folder opens it directly
tmcode .                  # Open current directory directly (from ~ this still shows the start page)
tmcode path/to/project    # Open a specific folder
tmcode path/to/main.py    # Open a specific file and its folder
```

## `/` Command Menu & Shortcuts

Open the menu anytime with **`Ctrl+K`**, or press **`/`** when focus is on the file tree or terminal log.

| Command | Shortcut | Action |
|---|---|---|
| `/run` | `F5` | Save and run active file in Terminal panel |
| `/stop` | `F7` | Stop currently running program |
| `/save` | `Ctrl+S` | Save active file |
| `/save-as` | `F8` | Save current buffer under a new name |
| `/new-file` | `Ctrl+N` | Create a new file in the selected folder |
| `/new-folder` | `F3` | Create a new folder in the selected folder |
| `/open-file` | `Ctrl+P` | Quick-select and open a file in this folder |
| `/open-folder` | `Ctrl+O` | Browse & open another folder as workspace root |
| `/rename` | `F2` | Rename selected file or folder |
| `/delete` | `F4` | Delete selected file or folder (with confirmation) |
| `/toggle-sidebar` | `Ctrl+B` | Show or hide the Explorer sidebar |
| `/toggle-terminal` | `Ctrl+J` | Show or hide the bottom Terminal panel |
| `/toggle-hidden` | `F9` | Show or hide hidden dotfiles in Explorer |
| `/terminal-grow` | `Ctrl+Up` | Increase Terminal panel height (or drag header bar) |
| `/terminal-shrink` | `Ctrl+Down` | Decrease Terminal panel height (or drag header bar) |
| `/copy-output` | `F10` | Copy selected or full Terminal output |
| `/clear-output` | `Ctrl+L` | Clear the Terminal output |
| `/next-tab` | `F11` | Switch to the next open file tab |
| `/close-tab` | `Ctrl+W` | Close the current file tab |
| `/split-editor` | `F12` | Toggle side-by-side split editor |
| `/quit` | `Ctrl+Q` | Exit TmCode |

## Running Tests

```bash
.venv/bin/pytest -v
```

## License

MIT © 2026 Abdullah Al Mamun. See [LICENSE](LICENSE).
