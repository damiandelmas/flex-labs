#!/usr/bin/env bash
# Install a labs module into flex's external module root (~/.flex/modules).
# Usage: ./scripts/install-module.sh <module-name>
set -euo pipefail

NAME="${1:-}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_ROOT/modules/$NAME"
DEST="${FLEX_HOME:-$HOME/.flex}/modules/$NAME"

if [ -z "$NAME" ] || [ ! -d "$SRC" ]; then
    echo "usage: $0 <module-name>"
    echo "available:"
    ls "$REPO_ROOT/modules/"
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
