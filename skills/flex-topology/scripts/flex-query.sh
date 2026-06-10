#!/usr/bin/env bash
# flex-query.sh — warm-path Flex MCP HTTP helper
# Usage: flex-query.sh <cell> <<< "query"
#
# Resilience: the MCP server kills queries that run past its 30s guard
# ({"error": "interrupted"}) — on a big cell this can happen once while the
# page cache is cold, and the same query passes in seconds on retry. The
# server also returns nothing for ~1-2 min while restarting. Both cases are
# retried here so callers see one of: result, or an honest final error.

CELL="${1:-claude_code}"
QUERY=$(cat)
PORT="${FLEX_MCP_PORT:-7134}"
URL="http://localhost:${PORT}/mcp/"

# Build JSON payload
PAYLOAD=$(python3 -c "
import json, sys
q = sys.stdin.read()
print(json.dumps({
  'jsonrpc': '2.0', 'method': 'tools/call', 'id': 2,
  'params': {'name': 'flex_search', 'arguments': {'query': q, 'cell': '$CELL'}}
}))
" <<< "$QUERY")

probe() {
  curl -s --max-time 5 -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"fq","version":"1.0"}},"id":1}' 2>/dev/null \
  | grep -q '"result"'
}

run_query() {
  curl -s -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "$PAYLOAD" 2>/dev/null \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('result',{}).get('content',[{}])[0].get('text',''))" 2>/dev/null
}

# Endpoint warm-up: tolerate a restarting server (probe every 5s, up to 6x).
UP=0
for _ in 1 2 3 4 5 6; do
  if probe; then UP=1; break; fi
  sleep 5
done
if [ "$UP" -ne 1 ]; then
  echo '{"error":"Flex MCP HTTP endpoint unavailable"}'
  exit 0
fi

OUT=$(run_query)

# Cold-cache guard trip: first run warmed the pages; one retry usually passes.
if printf '%s' "$OUT" | grep -q '"error": *"interrupted"'; then
  OUT=$(run_query)
fi

printf '%s\n' "$OUT"
