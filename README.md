# TmCode

Lightweight, cross-platform VS Code-style terminal code editor and runner for **Python**, **C**, and **C++**.

## Features

- Launch right from any terminal (`tmcode` from `~` opens the Home folder picker; `tmcode` inside any project folder opens it directly)
- VS Code Dark Modern 3-pane layout: Explorer with `+F`/`+D` buttons, multi-tab + side-by-side split editor (`F12`), and draggable/resizable Terminal panel
- VS Code Dark+ syntax colors across **17 languages** (Python, C, C++, JS/TS, HTML, CSS, JSON, Java, Go, Rust, Bash, SQL, Markdown, YAML, TOML, XML, Regex)
- Lightweight offline code suggestions (language builtins + open-file words) and automatic indentation while typing
- Auto-closing brackets and quotes like VS Code: `(` `[` `{` `"` `'` insert their partner, typing the closer steps over it, Backspace removes an empty pair, Enter inside `{}` opens an indented block, and typing a bracket with text selected wraps it
- Unified `/run` (`F5`) supporting **Python, C, C++, JavaScript, TypeScript, Bash, Go, Rust, Java, Ruby, PHP, Perl, and Lua** with live interactive keyboard input
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
