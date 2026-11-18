# TermCode

Lightweight, cross-platform VS Code-style terminal code editor and runner for **Python**, **C**, and **C++**.

## Features

- Launch right from any terminal (`termcode`, `termcode .`, `termcode path/to/folder`, or `termcode file.py`)
- Full-screen 3-pane layout: File/Folder Explorer, Syntax-Highlighted Editor, and Interactive Output Panel
- Unified `/run` (`F5`) that compiles & runs Python, C, and C++ with live output and built-in keyboard input (`input()`, `scanf`, `cin`) right inside the Output panel
- Searchable `/` command menu (`Ctrl+K` anywhere, or `/` outside text boxes) with a dedicated shortcut for every action
- Safe file & folder management (create files/folders inside the selected directory, quick-open files, switch folders, rename, delete with confirmation, unsaved-changes guard, binary-file guard)
- Cross-platform support for Linux, macOS, and Windows

## Installation

### Linux / macOS
```bash
cd /home/jobayer/Documents/termcode
python3 -m venv .venv
.venv/bin/pip install -e .
mkdir -p ~/.local/bin
ln -sf "$(pwd)/.venv/bin/termcode" ~/.local/bin/termcode
```

### Windows (PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\termcode.exe .
```
*(Or install globally with `pipx install .` on Linux, macOS, or Windows.)*

## Usage

```bash
termcode                  # Open current directory
termcode .                # Open current directory
termcode path/to/project  # Open a specific folder
termcode path/to/main.py  # Open a specific file and its folder
```

## `/` Command Menu & Shortcuts

Open the menu anytime with **`Ctrl+K`**, or press **`/`** when focus is on the file tree or output panel.

| Command | Shortcut | Action |
|---|---|---|
| `/run` | `F5` | Save and run active file in Output panel |
| `/stop` | `F7` | Stop currently running program |
| `/save` | `Ctrl+S` | Save active file |
| `/save-as` | `F8` | Save current buffer under a new name |
| `/new-file` | `Ctrl+N` | Create a new file in the selected folder |
| `/new-folder` | `F3` | Create a new folder in the selected folder |
| `/open-file` | `Ctrl+P` | Quick-select and open a file in this folder |
| `/open-folder` | `Ctrl+O` | Switch to another folder as workspace root |
| `/rename` | `F2` | Rename selected file or folder |
| `/delete` | `F4` | Delete selected file or folder (with confirmation) |
| `/toggle-sidebar` | `Ctrl+B` | Show or hide the file explorer sidebar |
| `/clear-output` | `Ctrl+L` | Clear the Output panel |
| `/quit` | `Ctrl+Q` | Exit TermCode |

## Running Tests

```bash
.venv/bin/pytest -v
```
