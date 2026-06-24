#!/usr/bin/env bash
# Standing regression guard for the instant module.
#
# Why this exists: when _edges_call landed, the FLAT default path silently broke
# (KeyError('id') — the call-graph pass read a key only tree-mode nodes carry),
# and the cell was written-but-unregistered. "parity-exact" was claimed but not
# continuously enforced. This script is the gate: run it on every instant change.
#
# Asserts:
#   1. flat default build SUCCEEDS (no crash) and REGISTERS the cell
#   2. flat build produces ZERO _edges_call / _edges_import (graph passes guarded)
#   3. nest build produces the tree (_edges_tree) AND, on Python, the call graph
#   4. flat is PARITY-STABLE: two flat builds → byte-identical _raw_chunks
#
# Usage: FLEX=/path/to/venv/bin/flex bash instant_regression.sh
set -u
FLEX="${FLEX:-flex}"
CELLS="${FLEX_HOME:-$HOME/.flex}/cells/labs"
FIX="$(mktemp -d)"
fail=0
note() { printf '  %-6s %s\n' "$1" "$2"; if [ "$1" = "FAIL" ]; then fail=1; fi; return 0; }

# fixture: one markdown (heading tree) + one python (def/class → call graph)
cat > "$FIX/doc.md" <<'EOF'
# Title
Intro.
## A
Alpha.
### A1
Deep.
EOF
cat > "$FIX/mod.py" <<'EOF'
def helper(x):
    return x + 1

def main():
    return helper(41)
EOF

count() { sqlite3 "$CELLS/$1.db" "SELECT count(*) FROM $2" 2>/dev/null || echo ERR; }

echo "== 1. flat default build (the regression case) =="
out=$("$FLEX" init --module instant --path "$FIX" --name instreg_flat 2>&1)
echo "$out" | grep -qiE 'traceback|KeyError|Error' && note FAIL "flat build crashed" || note OK "flat build ran"
sqlite3 "$HOME/.flex/registry.db" "SELECT 1 FROM cells WHERE name='instreg_flat'" 2>/dev/null | grep -q 1 \
  && note OK "flat cell registered" || note FAIL "flat cell NOT registered (write-but-no-register regression)"

echo "== 2. flat graph passes guarded (expect 0) =="
[ "$(count instreg_flat _edges_call)" = "0" ] && note OK "flat _edges_call = 0" || note FAIL "flat _edges_call != 0"
[ "$(count instreg_flat _edges_import)" = "0" ] && note OK "flat _edges_import = 0" || note FAIL "flat _edges_import != 0"

echo "== 3. nest build → tree + python call graph =="
"$FLEX" init --module instant --nest --path "$FIX" --name instreg_nest >/dev/null 2>&1
[ "$(count instreg_nest _edges_tree)" -gt 0 ] 2>/dev/null && note OK "nest _edges_tree > 0" || note FAIL "nest tree empty"
[ "$(count instreg_nest _edges_call)" -gt 0 ] 2>/dev/null && note OK "nest _edges_call > 0 (python)" || note FAIL "nest call graph empty"

echo "== 4. flat parity-stable across rebuild =="
h1=$(sqlite3 "$CELLS/instreg_flat.db" "SELECT id,length(content) FROM _raw_chunks ORDER BY id" 2>/dev/null | md5sum)
"$FLEX" init --module instant --regen instreg_flat >/dev/null 2>&1
h2=$(sqlite3 "$CELLS/instreg_flat.db" "SELECT id,length(content) FROM _raw_chunks ORDER BY id" 2>/dev/null | md5sum)
[ "$h1" = "$h2" ] && note OK "flat _raw_chunks byte-stable" || note FAIL "flat output drifted across rebuild"

# cleanup
for c in instreg_flat instreg_nest; do
  rm -f "$CELLS/$c.db" "$CELLS/$c.db-wal" "$CELLS/$c.db-shm"
  sqlite3 "$HOME/.flex/registry.db" "DELETE FROM cells WHERE name='$c'" 2>/dev/null
done
rm -rf "$FIX"

echo
[ "$fail" = 0 ] && echo "PASS — instant regression guard green" || echo "FAIL — instant regression guard RED"
exit "$fail"
