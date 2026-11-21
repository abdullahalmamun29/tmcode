# TmCode

Lightweight, cross-platform VS Code-style terminal code editor for **any language**: open, highlight and run code right in your terminal.

**Website:** https://abdullahalmamun29.github.io/tmcode/

## Features

- Launch right from any terminal (`tmcode` from `~` opens the Home folder picker; `tmcode` inside any project folder opens it directly)
- VS Code Dark Modern 3-pane layout: Explorer with `+F`/`+D` buttons, multi-tab + side-by-side split editor (`Ctrl+\`), and draggable/resizable Terminal panel
- Syntax colors for **hundreds of languages**: precise tree-sitter highlighting for Python, C, C++, JS/TS, HTML, CSS, JSON, Java, Go, Rust, Bash, SQL, Markdown, YAML, TOML, XML and Regex, plus Pygments for everything else (Kotlin, Swift, C#, Haskell, Elixir, Zig, Dart, R, Julia, Dockerfile, ...)
- **Smart editing with language servers**, like VS Code: errors and warnings underlined as you type (the message shows in the status bar), real completions (`os.` lists what's in `os`), `F1` for types and docs, `F12` or `Ctrl+click` to go to a definition and `Alt+Left` to come back. Uses the standard servers you already have: pyright, clangd, typescript-language-server, rust-analyzer, gopls, texlab and more (see [Language servers](#language-servers))
- Offline code suggestions (language builtins + open-file words) when no server is installed, and automatic indentation while typing
- Auto-closing brackets and quotes like VS Code: `(` `[` `{` `"` `'` insert their partner, typing the closer steps over it, Backspace removes an empty pair, Enter inside `{}` opens an indented block, and typing a bracket with text selected wraps it
- Run any language with `F5` (live keyboard input) or `Shift+F5` (full terminal): Python, C, C++, JavaScript, TypeScript, Bash, Go, Rust, Java, Ruby, PHP, Perl, Lua, Kotlin, Swift, Dart, C#, F#, Haskell, Elixir, Erlang, R, Julia, Scala, Zig, Nim, Crystal, D, Fortran, Pascal, Ada, COBOL, OCaml, Clojure, Groovy, PowerShell, Zsh, Fish, Tcl, Racket, Scheme, Common Lisp, Raku, AWK, Octave, CoffeeScript, Mojo, Odin, Vala and SQL (SQLite). Any script with a `#!` line runs too, and you can add your own commands (see [Run any language](#run-any-language))
- Web files too: `F5` on an `.html` page opens it in your browser (on a `.css`/`.scss` file it opens the page next to it), `.jsx`/`.tsx` run with `tsx`, `bun` or `deno`, and HTML, CSS, SCSS, Less, Vue, Svelte, Astro, PHP, EJS, Nunjucks and Handlebars all get colors. For framework projects (React, Vue, Next.js...) press `Ctrl+T` and run `npm run dev` in your own shell
- VS Code editing keys: move/copy lines with `Alt+Up/Down`, `Ctrl+/` comments, `Shift+Delete` cuts the line, `Tab`/`Shift+Tab` indent blocks, `Ctrl+D`, smart `Home` and more (see [Editing keys](#editing-keys-vs-code-style))
- Compiled programs are kept in a hidden `.tmcode` folder, so running an unchanged file again starts instantly
- LaTeX: `F5` on a `.tex` file builds the PDF (with `latexmk`, or `pdflatex`/`xelatex`/`lualatex`, or `tectonic`) and opens it. It picks XeLaTeX for `fontspec`/`polyglossia` documents (Bangla, Arabic, CJK...), honors `% !TEX program = ...` and `% !TEX root = ...`, and on a chapter file builds the main document next to it
- Update prompt on start, like Codex: **Update now**, **Skip**, or **Skip until next version** (checks at most once a day; turn off with `TMCODE_NO_UPDATE_CHECK=1`). `tmcode --update` updates any time
- Real OS clipboard integration (Linux X11/Wayland, macOS, Windows), `Ctrl+A` select-all, and VS Code-style `Ctrl+C` (copies selection or current line)
- Searchable `/` command menu (`Ctrl+K` anywhere, or `/` outside text inputs) with a shortcut for every action

## Installation

One command, no Python needed:

**macOS / Linux**
```bash
curl -fsSL https://abdullahalmamun29.github.io/tmcode/install.sh | sh
```

**Windows** (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -c "irm https://abdullahalmamun29.github.io/tmcode/install.ps1 | iex"
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

## Language servers

TmCode talks to the same language servers VS Code and Neovim use. It starts the right one when you open a file, if it's installed, and tells you once how to install it if it isn't.

| Language | Server | Install |
|---|---|---|
| Python | basedpyright, pyright, pylsp or jedi-language-server | `pipx install basedpyright` or `npm install -g pyright` |
| C, C++ | clangd | `sudo apt install clangd`, `brew install llvm`, or `pipx install clangd` |
| JavaScript, TypeScript | typescript-language-server (TypeScript 7: `tsc --lsp`) | `npm install -g typescript-language-server typescript` |
| Rust | rust-analyzer | `rustup component add rust-analyzer` |
| Go | gopls | `go install golang.org/x/tools/gopls@latest` |
| LaTeX, BibTeX | texlab | `cargo install texlab` or `brew install texlab` |
| Bash | bash-language-server | `npm install -g bash-language-server` |
| HTML, CSS, JSON | vscode-langservers-extracted | `npm install -g vscode-langservers-extracted` |
| Lua, Java, Kotlin, Dart, Zig, Haskell, Ruby, PHP, YAML, TOML, C#, Swift, OCaml | lua-language-server, jdtls, kotlin-language-server, dart, zls, hls, ruby-lsp, intelephense, yaml-language-server, taplo, csharp-ls, sourcekit-lsp, ocamllsp | see each project |

What you get:

- **Problems as you type:** errors underlined in red, warnings in yellow; the message for the cursor's line shows in the status bar, with counts on the right. `/problems` lists them all.
- **Smart completion:** suggestions from the server (members after `.`, imports, your own functions), falling back to simple word suggestions when there is no server.
- **`F1`** shows the type, signature and docs of what's under the cursor (and the full error message).
- **`F12`** or **`Ctrl+click`** goes to the definition, even in another file; **`Alt+Left`** goes back.
- `/language-server` tells you which server checks the current file, or how to install one.

To use a different server, or turn one off, create `lsp.json` next to `run-commands.json` (`~/.config/termcode/` on Linux):

```json
{
  ".py": "pylsp",
  ".rs": ["ra-multiplex", "client"],
  ".tex": false
}
```

`"*": false` turns language servers off everywhere (or set `TMCODE_NO_LSP=1`).

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
| `/split-editor` | `Ctrl+\` | Toggle side-by-side split editor |
| `/go-to-definition` | `F12` | Jump to the definition of the symbol under the cursor (or `Ctrl+click`) |
| `/go-back` | `Alt+Left` | Back to where you were before going to a definition |
| `/info` | `F1` | Type, signature and docs of the symbol under the cursor |
| `/problems` | | List errors and warnings in this file and jump to one |
| `/language-server` | | Which language server checks this file, or how to install one |
| `/toggle-comment` | `Ctrl+/` | Comment or uncomment the line or selection |
| `/word-wrap` | `Alt+Z` | Wrap long lines on or off |
| `/clean-build` | | Delete the saved programs next to this file (the next run recompiles) |
| `/quit` | `Ctrl+Q` | Exit TmCode |

### Editing keys (VS Code style)

| Keys | Action |
|---|---|
| `Ctrl+C` / `Ctrl+X` / `Shift+Delete` | Copy / cut the selection, or the whole line when nothing is selected |
| `Ctrl+Shift+K` | Delete the line |
| `Alt+Up` / `Alt+Down` | Move the line (or selected lines) up / down |
| `Alt+Shift+Up` / `Alt+Shift+Down` | Copy the line (or selected lines) up / down |
| `Ctrl+/` | Toggle comment, with the right marker for the language (`#`, `//`, `--`, `%`, `<!-- -->`...) |
| `Tab` / `Shift+Tab` | Indent / outdent the selected lines |
| `Ctrl+D` | Select the word; press again to jump to its next match |
| `Home` | First non-blank character; press again for column 0 |
| `Ctrl+Home` / `Ctrl+End` | Top / end of the file (add `Shift` to select) |
| `Ctrl+Backspace` / `Ctrl+Delete` | Delete the word left / right |
| `Ctrl+Enter` / `Ctrl+Shift+Enter` | New line below / above (only in terminals that report these keys, e.g. kitty, WezTerm, Ghostty) |
| `Ctrl+Z` / `Ctrl+Y` or `Ctrl+Shift+Z` | Undo / redo |
| `Alt+Z` | Word wrap on / off |

### Faster re-runs

Compiled languages (C, C++, Rust, Kotlin, Fortran, Pascal, Ada...) build into a hidden `.tmcode` folder next to your file. Press `F5` again without changing anything and TmCode runs the previous program straight away instead of compiling again. Editing the file, or a local header it includes, triggers a fresh build. The folder ignores itself in git; delete it any time (or run `/clean-build`).

## Running Tests

```bash
.venv/bin/pytest -v
```

## License

MIT © 2026 Abdullah Al Mamun. See [LICENSE](LICENSE).
