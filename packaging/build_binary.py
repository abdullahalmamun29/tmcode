"""Build a standalone TmCode folder + archive with PyInstaller.

Usage (from the repo root, in an environment where tmcode is installed):
    pip install . pyinstaller
    python packaging/build_binary.py

Produces dist/tmcode-<os>-<arch>.tar.gz (or .zip on Windows) containing a
`tmcode/` folder whose `tmcode` / `tmcode.exe` runs without Python installed.
"""

from __future__ import annotations

import importlib.util
import pkgutil
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller"


def target_name() -> str:
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"tmcode-{system}-{arch}"


def tree_sitter_grammars() -> list[str]:
    # Textual imports grammars by name at runtime, so PyInstaller can't see them.
    return sorted(
        m.name for m in pkgutil.iter_modules()
        if m.name.startswith("tree_sitter_") and importlib.util.find_spec(m.name)
    )


def build() -> Path:
    args = [
        str(ROOT / "packaging" / "entry.py"),
        "--name", "tmcode",
        "--onedir",            # starts fast: nothing to unpack on every launch
        "--console",
        "--noconfirm",
        "--clean",
        "--distpath", str(WORK / "out"),
        "--workpath", str(WORK / "work"),
        "--specpath", str(WORK),
        "--collect-all", "textual",
        "--collect-data", "termcode",
        "--collect-submodules", "termcode",
        "--collect-submodules", "rich",
        # Pygments loads its ~500 language lexers by name: bundle them all.
        "--collect-submodules", "pygments.lexers",
        "--collect-submodules", "pygments.styles",
    ]
    for grammar in tree_sitter_grammars():
        args += ["--collect-all", grammar]
    PyInstaller.__main__.run(args)

    folder = WORK / "out" / "tmcode"
    DIST.mkdir(exist_ok=True)
    name = target_name()
    if sys.platform == "win32":
        archive = DIST / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in folder.rglob("*"):
                zf.write(path, Path("tmcode") / path.relative_to(folder))
    else:
        archive = DIST / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(folder, arcname="tmcode")
    print(f"built {archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    return archive


if __name__ == "__main__":
    shutil.rmtree(WORK, ignore_errors=True)
    build()
