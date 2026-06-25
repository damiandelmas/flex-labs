#!/usr/bin/env bash
# build_product_code.sh <product> — build <product>-code from a runway product's map.md.
#
# RUNWAY-SPECIFIC SIDECAR (not core). It reads the canonical place->repo bindings from
# the product's map.md `repos:` block and loops the GENERAL instant code-cell command
# over them. The core capability is general and needs none of this:
#
#     flex init --module instant --nest --code --path <repo> --name <cell>
#
# This wrapper just knows WHICH repos to point it at, for a runway workspace.
#
# Usage: build_product_code.sh flexchat
set -euo pipefail
PROD="${1:?usage: build_product_code.sh <product>}"
CELL="${PROD}-code"
FLEX="${FLEX_BIN:-/home/axp/projects/flexsearch/main/venv/bin/flex}"
RUNWAY="$HOME/.runway/$PROD/places/.context/current"

# locate map.md (control-panel projection of the per-place location.json latches)
MAP=""
for c in "$RUNWAY/.control/map.md" "$RUNWAY/map.md"; do
  [ -f "$c" ] && MAP="$c" && break
done
[ -n "$MAP" ] || { echo "no map.md under $RUNWAY"; exit 1; }

# extract $P/... repo paths from the repos: block only (homes uses $R, infra/urls don't use $P)
repos=$(awk '/^repos:/{f=1;next} /^[a-zA-Z_]+:/{f=0} f' "$MAP" \
        | grep -oE '\$P/[^ #]+' | sed "s#\\\$P#$HOME/projects#" | sort -u)
[ -n "$repos" ] || { echo "no \$P repos in $MAP"; exit 1; }

echo "building $CELL from $MAP:"
n=0
while IFS= read -r r; do
  [ -d "$r" ] || { echo "  skip (missing): $r"; continue; }
  echo "  + $r"
  "$FLEX" init --module instant --nest --code --path "$r" --name "$CELL" >/dev/null 2>&1
  n=$((n+1))
done <<< "$repos"
echo "$CELL: $n repos indexed"
