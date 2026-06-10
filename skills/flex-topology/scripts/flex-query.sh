#!/usr/bin/env bash
# flex-query.sh — warm-path Flex MCP HTTP helper
# Usage: flex-query.sh <cell> <<< "query"

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

# Try warm path
RESULT=$(curl -s --max-time 5 -X POST "$URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"fq","version":"1.0"}},"id":1}' 2>/dev/null)

if echo "$RESULT" | grep -q '"result"' 2>/dev/null; then
  curl -s -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "$PAYLOAD" 2>/dev/null \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('result',{}).get('content',[{}])[0].get('text',''))" 2>/dev/null
else
  echo '{"error":"Flex MCP HTTP endpoint unavailable"}'
fi
