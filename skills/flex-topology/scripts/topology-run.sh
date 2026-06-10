#!/usr/bin/env bash
# topology-run.sh — single-shot topology: seeds communities and runs every
# downstream query sequentially, emitting section headers so the slash command
# only needs ONE shell expansion. This eliminates the seed/query race that
# plagued the previous split-script design.
#
# Usage: topology-run.sh [DAYS]
#
# Requires: flex >= 0.31 with claude_code cell indexed.
# The flex MCP server must be reachable at localhost:${FLEX_MCP_PORT:-7134}
# (set FLEX_MCP_PORT to override). All SQL query files are resolved relative
# to this script's directory — no hardcoded absolute paths.

set -euo pipefail

DAYS="${1:-14}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SEED="$SCRIPT_DIR/topology-seed.sh"
QUERY="$SCRIPT_DIR/topology-query.sh"
QUERY_DIR="$SCRIPT_DIR/../queries/topology"

run_section() {
  local heading="$1"
  local sql_file="$2"
  printf '\n## %s\n\n' "$heading"
  "$QUERY" "$sql_file"
}

# Invalidate any stale cache from a previous run BEFORE seeding, so no one can
# accidentally read old community IDs.
rm -f /tmp/topology-community-ids.txt

printf '## Seed (last %s days)\n\n' "$DAYS"
"$SEED" "$DAYS"

run_section "Communities (all-time, seeded)"          "$QUERY_DIR/seed-communities.sql"
run_section "Hubs (top 3 per community)"              "$QUERY_DIR/seed-hubs.sql"
run_section "Hub Content (top hub opener + fingerprint per community)" "$QUERY_DIR/seed-hub-openers.sql"
run_section "Bridges (cross-community)"               "$QUERY_DIR/seed-bridges.sql"
run_section "Projects"                                "$QUERY_DIR/seed-projects.sql"
run_section "Temporal Pulse"                          "$QUERY_DIR/seed-temporal.sql"
