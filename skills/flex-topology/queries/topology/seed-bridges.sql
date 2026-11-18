!WITH recent_bridges AS (
  SELECT session_id, title, fingerprint_index, community_label, centrality, message_count, started_at
  FROM sessions
  WHERE is_bridge = 1
    AND community_id IN ({{COMMUNITY_IDS}})
    AND started_at >= date('now', '-{{DAYS}} days')
  ORDER BY centrality DESC
  LIMIT 6
),
alltime_bridges AS (
  SELECT session_id, title, fingerprint_index, community_label, centrality, message_count, started_at
  FROM sessions
  WHERE is_bridge = 1
    AND community_id IN ({{COMMUNITY_IDS}})
    AND session_id NOT IN (SELECT session_id FROM recent_bridges)
  ORDER BY centrality DESC
  LIMIT 4
),
top_bridges AS (
  SELECT *, 'recent' as tier FROM recent_bridges
  UNION ALL
  SELECT *, 'all-time' as tier FROM alltime_bridges
)
SELECT json_group_array(json_object(
  'session_id', session_id,
  'title', SUBSTR(title, 1, 120),
  'tier', tier,
  'fingerprint', SUBSTR(COALESCE(fingerprint_index, ''), 1, 2000),
  'community', community_label,
  'centrality', ROUND(centrality, 6),
  'msgs', message_count,
  'started', started_at
))
FROM top_bridges
