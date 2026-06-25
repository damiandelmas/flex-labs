#!/usr/bin/env bash
# build_product_code.sh <product> [group]
#
# RUNWAY SIDECAR (not the core capability). Builds one code cell per top-level
# place-GROUP of a runway product, from the `.context/about/repo.json` latches.
# Each cell = <product>-<group>-code = instant --nest --code over the union of
# every repo.json under places/<group>/. It is the 1:1 partner of the per-group
# context cell.
#
# The GENERAL capability needs none of this — any user, any repo:
#     flex init --module instant --nest --code --path /any/repo --name mycode
# This wrapper just reads which repos a runway group binds, and loops that command.
#
# Usage:
#   build_product_code.sh flexchat            # all groups of flexchat
#   build_product_code.sh flex engine         # just flex-engine-code
set -uo pipefail

PROD="${1:?usage: build_product_code.sh <product> [group]}"
ONLY_GROUP="${2:-}"
FLEX="${FLEX:-${FLEX_BIN:-/home/axp/projects/flexsearch/main/venv/bin/flex}}"
RW="$HOME/.runway/$PROD/places"
REG="$HOME/.flex/registry.db"

[ -d "$RW" ] || { echo "no such product tree: $RW" >&2; exit 1; }

# group|repo lines, grouped by top-level place-group, unioned+deduped from repo.json
mapfile -t LINES < <(python3 - "$RW" <<'PY'
import json, glob, sys
base = sys.argv[1]
groups = {}
for rj in set(glob.glob(f"{base}/*/**/.context/about/repo.json", recursive=True)
              + glob.glob(f"{base}/*/.context/about/repo.json")):
    group = rj[len(base)+1:].split("/")[0]
    for r in json.load(open(rj)):
        groups.setdefault(group, dict())[r] = None   # dedup, preserve order
for g in sorted(groups):
    for r in groups[g]:
        print(f"{g}|{r}")
PY
)

declare -A SEEN
for line in "${LINES[@]}"; do
  group="${line%%|*}"; repo="${line#*|}"
  [ -n "$ONLY_GROUP" ] && [ "$group" != "$ONLY_GROUP" ] && continue
  cell="${PROD}-${group}-code"
  if [ -z "${SEEN[$cell]:-}" ]; then
    rm -f "$HOME/.flex/cells/labs/${cell}.db"*                       # clean union
    sqlite3 "$REG" "DELETE FROM cells WHERE name='${cell}'" 2>/dev/null
    SEEN[$cell]=1
    echo ">> $cell"
  fi
  [ -d "$repo" ] || { echo "   ! missing repo: $repo" >&2; continue; }
  echo "   + $repo"
  "$FLEX" init --module instant --nest --code --path "$repo" --name "$cell" >/dev/null 2>&1 \
    || echo "   ! build failed: $repo" >&2
done
echo "done: $PROD${ONLY_GROUP:+/$ONLY_GROUP}"
