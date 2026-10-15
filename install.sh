#!/bin/sh
# One-shot installer for gitx.
#
# Makes sure a Python 3.10+ interpreter is available (installing one
# through Homebrew when needed), creates a private virtualenv next to
# this script, installs pygit2 into it and drops a `gitx` launcher into
# ~/.local/bin, so the CLI can be run from any repository without
# touching the system Python.
#
# Usage:
#   sh install.sh
#
# Re-running is safe: existing pieces are detected and kept.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

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

exec "$py" "$here/gitx.py" install
