#!/bin/sh
# TmCode installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/abdullahalmamun29/tmcode/main/install.sh | sh
#
# Downloads the prebuilt TmCode for this computer from GitHub Releases (no
# Python needed). If there is no prebuilt file for this system, it installs
# TmCode with uv instead, which also fetches Python by itself.
#
# Settings (optional environment variables):
#   TMCODE_INSTALL_DIR      where the app goes   (default ~/.local/share/tmcode)
#   TMCODE_BIN_DIR          where the command goes (default ~/.local/bin)
#   TMCODE_NO_MODIFY_PATH=1 never edit shell startup files
#
# Uninstall:  rm -rf ~/.local/share/tmcode ~/.local/bin/tmcode ~/.local/bin/termcode

set -eu

REPO="abdullahalmamun29/tmcode"
RELEASES="${TMCODE_RELEASES_URL:-https://github.com/$REPO/releases/latest/download}"
INSTALL_DIR="${TMCODE_INSTALL_DIR:-$HOME/.local/share/tmcode}"
BIN_DIR="${TMCODE_BIN_DIR:-$HOME/.local/bin}"
USER_PATH="$PATH"  # PATH as the user has it, before we add anything

say() { printf '%s\n' "$*"; }
warn() { printf 'tmcode-install: %s\n' "$*" >&2; }

download() { # url dest
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 2 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$2" "$1"
    else
        warn "need curl or wget"; return 1
    fi
}

asset_name() {
    case "$(uname -s)" in
        Linux) os=linux ;;
        Darwin) os=macos ;;
        *) return 1 ;;
    esac
    case "$(uname -m)" in
        x86_64 | amd64) arch=x86_64 ;;
        arm64 | aarch64) arch=arm64 ;;
        *) return 1 ;;
    esac
    # A Mac with Apple Silicon can report x86_64 inside a Rosetta shell.
    if [ "$os" = macos ] && [ "$arch" = x86_64 ] &&
        [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
        arch=arm64
    fi
    printf 'tmcode-%s-%s.tar.gz' "$os" "$arch"
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
    else return 1
    fi
}

link_command() { # name
    target="$BIN_DIR/$1"
    # Keep a `termcode` someone set up themselves (e.g. a dev checkout).
    if [ -e "$target" ] || [ -L "$target" ]; then
        case "$(readlink "$target" 2>/dev/null || true)" in
            "$INSTALL_DIR"/*) ;;
            *) [ "$1" = tmcode ] || return 0 ;;
        esac
    fi
    ln -sf "$INSTALL_DIR/tmcode/tmcode" "$target"
}

install_binary() {
    asset="$(asset_name)" || { warn "no prebuilt TmCode for $(uname -s) $(uname -m)"; return 1; }
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    say "Downloading $asset ..."
    download "$RELEASES/$asset" "$tmp/$asset" || { warn "download failed"; return 1; }

    if download "$RELEASES/SHA256SUMS" "$tmp/SHA256SUMS" 2>/dev/null; then
        expected="$(grep " $asset\$" "$tmp/SHA256SUMS" | cut -d' ' -f1 || true)"
        actual="$(sha256_of "$tmp/$asset" || true)"
        if [ -n "$expected" ] && [ -n "$actual" ] && [ "$expected" != "$actual" ]; then
            warn "checksum mismatch for $asset, not installing it"
            return 1
        fi
    fi

    tar -xzf "$tmp/$asset" -C "$tmp"
    # Make sure it actually runs here (e.g. a very old Linux).
    "$tmp/tmcode/tmcode" --version >/dev/null 2>&1 || { warn "prebuilt TmCode doesn't run on this system"; return 1; }

    mkdir -p "$INSTALL_DIR" "$BIN_DIR"
    rm -rf "$INSTALL_DIR/tmcode"
    mv "$tmp/tmcode" "$INSTALL_DIR/tmcode"
    link_command tmcode
    link_command termcode
    return 0
}

install_with_uv() {
    say "Installing TmCode with uv instead ..."
    if ! command -v uv >/dev/null 2>&1; then
        if [ -x "$HOME/.local/bin/uv" ]; then
            PATH="$HOME/.local/bin:$PATH"
        else
            tmp_uv="$(mktemp)"
            download https://astral.sh/uv/install.sh "$tmp_uv"
            UV_NO_MODIFY_PATH=1 sh "$tmp_uv"
            rm -f "$tmp_uv"
            PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        fi
    fi
    uv tool install --force --python 3.12 tmcode ||
        uv tool install --force --python 3.12 "git+https://github.com/$REPO.git"
    BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "$BIN_DIR")"
}

add_to_path() {
    case ":$USER_PATH:" in *":$BIN_DIR:"*) return 0 ;; esac
    if [ "${TMCODE_NO_MODIFY_PATH:-0}" = 1 ]; then
        say "Add $BIN_DIR to your PATH to use the tmcode command."
        return 0
    fi
    line="export PATH=\"$BIN_DIR:\$PATH\""
    case "$(basename "${SHELL:-sh}")" in
        zsh) rc="$HOME/.zshrc" ;;
        bash) if [ "$(uname -s)" = Darwin ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
        fish)
            mkdir -p "$HOME/.config/fish/conf.d"
            printf 'fish_add_path "%s"\n' "$BIN_DIR" > "$HOME/.config/fish/conf.d/tmcode.fish"
            say "Added $BIN_DIR to your PATH. Open a new terminal to use tmcode."
            return 0 ;;
        *) rc="$HOME/.profile" ;;
    esac
    if ! grep -qsF "$line" "$rc"; then
        printf '\n# Added by the TmCode installer\n%s\n' "$line" >> "$rc"
    fi
    say "Added $BIN_DIR to your PATH in $rc. Open a new terminal to use tmcode."
}

main() {
    if install_binary; then
        say "Installed TmCode to $INSTALL_DIR/tmcode"
    else
        install_with_uv
        say "Installed TmCode with uv"
    fi
    add_to_path
    say ""
    say "Done! Run:  tmcode          (inside a project folder)"
}

main "$@"
