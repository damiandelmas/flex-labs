!SELECT json_group_array(json_object(
  'session_id', session_id,
  'title', SUBSTR(title, 1, 120),
  'fingerprint', COALESCE(fingerprint_index, ''),
  'community', community_label,
  'centrality', ROUND(centrality, 6),
  'msgs', message_count,
  'started', started_at
))
FROM sessions
WHERE is_bridge = 1
  AND community_id IN ({{COMMUNITY_IDS}})
ORDER BY centrality DESC
LIMIT 8
