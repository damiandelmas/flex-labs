!SELECT json_group_array(json_object(
  'community', community_label,
  'community_id', community_id,
  'session_id', session_id,
  'title', SUBSTR(title, 1, 120),
  'fingerprint', COALESCE(fingerprint_index, ''),
  'centrality', ROUND(centrality, 6),
  'msgs', message_count,
  'started', started_at
))
FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY community_id ORDER BY centrality DESC) as rn
  FROM sessions
  WHERE is_hub = 1
    AND community_id IN ({{COMMUNITY_IDS}})
)
WHERE rn <= 3
ORDER BY community_id, centrality DESC
