#!/bin/sh
# One-shot installer for gitx.
#
# Two ways to run it:
#
#   # without a repository checkout
#   curl -fsSL https://raw.githubusercontent.com/omgseven/gitx/main/install.sh | sh
#
#   # from a repository checkout
#   sh install.sh
#
# With a gitx.py next to this script that copy is installed in place;
# without one gitx.py is downloaded into ~/.local/share/gitx first.
# Either way the installer makes sure a Python 3.10+ interpreter is
# available (installing one through Homebrew when needed), creates a
# private virtualenv, installs pygit2 into it and drops a `gitx`
# launcher into ~/.local/bin.
#
# Re-running is safe: existing pieces are detected and kept.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -f "$here/gitx.py" ]; then
    payload="$here/gitx.py"
else
    share="${XDG_DATA_HOME:-$HOME/.local/share}/gitx"
    payload="$share/gitx.py"
    mkdir -p "$share"
    tmp="$payload.tmp.$$"
    raw="https://raw.githubusercontent.com/omgseven/gitx/main/gitx.py"
    api="https://api.github.com/repos/omgseven/gitx/contents/gitx.py?ref=main"
    echo "gitx: no local gitx.py found; downloading it from GitHub ..."
    if ! curl -fsSL "$raw" -o "$tmp" 2>/dev/null \
            && ! curl -fsSL -H "Accept: application/vnd.github.raw" "$api" -o "$tmp"; then
        rm -f "$tmp"
        echo "gitx: could not download gitx.py from GitHub" >&2
        exit 1
    fi
    mv "$tmp" "$payload"
fi

# print the first interpreter on PATH that runs Python 3.10+
find_python() {
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
        if command -v "$candidate" >/dev/null 2>&1 \
                && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

py=$(find_python || true)

if [ -z "$py" ] && command -v brew >/dev/null 2>&1; then
    echo "gitx: no Python 3.10+ found; installing it with Homebrew ..."
    brew install python
    hash -r 2>/dev/null || true
    py=$(find_python || true)
fi

if [ -z "$py" ]; then
    echo "gitx: Python 3.10+ is required (pygit2 wheels start there)." >&2
    if command -v brew >/dev/null 2>&1; then
        echo "gitx: run 'brew install python', then re-run this script." >&2
    else
        echo "gitx: install Python 3.10+ from python.org or your package" >&2
        echo "gitx: manager, then re-run this script." >&2
    fi
    exit 1
fi

exec "$py" "$payload" install
