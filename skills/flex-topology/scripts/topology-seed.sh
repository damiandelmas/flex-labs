#!/usr/bin/env bash
# topology-seed.sh — Phase 1: seed communities from recent activity, cache IDs
# Usage: topology-seed.sh [DAYS]
# Writes community IDs to /tmp/topology-community-ids.txt

set -euo pipefail

DAYS="${1:-14}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QUERY_DIR="$SCRIPT_DIR/../queries/topology"
FLEX_QUERY="$SCRIPT_DIR/flex-query.sh"
CACHE="/tmp/topology-community-ids.txt"

# NOTE: leading '!' in the SQL file is the flex_search gate-bypass token.
# Preserve it — do not strip.
SEED_SQL=$(sed "s/{{DAYS}}/$DAYS/g" "$QUERY_DIR/seed.sql")
SEED_RESULT=$(echo "$SEED_SQL" | "$FLEX_QUERY" claude_code 2>/dev/null)

COMMUNITY_IDS=$(echo "$SEED_RESULT" | python3 -c "
import json, sys, re
raw = sys.stdin.read()
lines = raw.strip().split('\n')
json_lines = []
started = False
for line in lines:
    if not started and line.strip().startswith('['):
        if re.match(r'^\[[\d]+ rows?,', line.strip()):
            continue
        started = True
    if started:
        json_lines.append(line)
try:
    data = json.loads('\n'.join(json_lines))
    first = data[0]
    if isinstance(first, dict) and len(first) == 1:
        inner = json.loads(list(first.values())[0])
    else:
        inner = data
    ids = [str(item['id']) for item in inner if item.get('id') is not None]
    print(','.join(ids))
except:
    pass
" 2>/dev/null)

if [ -z "$COMMUNITY_IDS" ]; then
  echo "SEED_FAILED — could not extract community IDs from seed result:"
  echo "$SEED_RESULT" | head -20
  exit 1
fi

echo "$COMMUNITY_IDS" > "$CACHE"
echo "$SEED_RESULT"
