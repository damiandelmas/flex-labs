#!/usr/bin/env bash
# topology-query.sh — Phase 2: run a single topology query with cached community IDs
# Usage: topology-query.sh <query-file.sql>
# Reads community IDs from /tmp/topology-community-ids.txt

set -euo pipefail

SQL_FILE="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FLEX_QUERY="$SCRIPT_DIR/flex-query.sh"
CACHE="/tmp/topology-community-ids.txt"

# Wait up to 60s for the seed step to populate the cache (slash command runs
# seed + queries in parallel; queries can fire before seed writes the file).
for _ in $(seq 1 300); do
  [ -s "$CACHE" ] && break
  sleep 0.2
done

if [ ! -s "$CACHE" ]; then
  echo "No cached community IDs. Run topology-seed.sh first."
  exit 1
fi

COMMUNITY_IDS=$(cat "$CACHE")
# NOTE: SQL files may start with '!' — that's the flex_search gate-bypass
# token (needed for the large hub/bridge/opener payloads). Do NOT strip it.
SQL=$(sed "s/{{COMMUNITY_IDS}}/$COMMUNITY_IDS/g" "$SQL_FILE")
echo "$SQL" | "$FLEX_QUERY" claude_code 2>/dev/null
