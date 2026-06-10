#!/usr/bin/env bash
# Install a labs module into flex's external module root (~/.flex/modules).
# Usage: ./scripts/install-module.sh <module-name>
set -euo pipefail

NAME="${1:-}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_ROOT/modules/$NAME"
# Agent modules live under modules/agents/<name> but flex discovers external
# modules one level deep, so the install destination is always flat.
if [ -n "$NAME" ] && [ ! -d "$SRC" ] && [ -d "$REPO_ROOT/modules/agents/$NAME" ]; then
    SRC="$REPO_ROOT/modules/agents/$NAME"
fi
DEST="${FLEX_HOME:-$HOME/.flex}/modules/$(basename "$NAME")"

if [ -z "$NAME" ] || [ ! -d "$SRC" ]; then
    echo "usage: $0 <module-name>"
    echo "available:"
    find "$REPO_ROOT/modules" -maxdepth 2 -name install.py | sed "s|$REPO_ROOT/modules/||;s|/install.py||"
    exit 1
fi

mkdir -p "$(dirname "$DEST")"
if [ -d "$DEST" ]; then
    echo "updating existing install at $DEST"
    rm -rf "$DEST"
fi
cp -r "$SRC" "$DEST"

echo "installed: $NAME -> $DEST"
echo "next:      flex init --module $NAME --help"
