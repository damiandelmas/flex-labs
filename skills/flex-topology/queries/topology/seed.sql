!SELECT json_group_array(json_object(
  'id', community_id,
  'label', community_label,
  'recent_sessions', sessions,
  'recent_msgs', total_msgs,
  'projects', projects
))
FROM (
  SELECT community_id, community_label,
    COUNT(*) as sessions,
    SUM(message_count) as total_msgs,
    GROUP_CONCAT(DISTINCT project) as projects
  FROM sessions
  WHERE started_at >= date('now', '-{{DAYS}} days')
    AND community_id IS NOT NULL
  GROUP BY community_id, community_label
  HAVING sessions >= 5
  ORDER BY sessions DESC
)
