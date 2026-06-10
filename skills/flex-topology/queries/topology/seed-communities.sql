!SELECT json_group_array(json_object(
  'id', community_id,
  'label', community_label,
  'sessions', sessions,
  'total_msgs', total_msgs,
  'hubs', hubs,
  'bridges', bridges,
  'avg_centrality', ROUND(avg_centrality, 6),
  'newest', newest,
  'oldest', oldest
))
FROM (
  SELECT community_id, community_label,
    COUNT(*) as sessions,
    SUM(message_count) as total_msgs,
    SUM(is_hub) as hubs,
    SUM(is_bridge) as bridges,
    AVG(centrality) as avg_centrality,
    MAX(started_at) as newest,
    MIN(started_at) as oldest
  FROM sessions
  WHERE community_id IN ({{COMMUNITY_IDS}})
  GROUP BY community_id, community_label
  ORDER BY sessions DESC
)
