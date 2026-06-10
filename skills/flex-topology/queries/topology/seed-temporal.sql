!SELECT json_group_array(json_object(
  'week_start', week_start,
  'week_end', week_end,
  'community', community_label,
  'sessions', sessions,
  'msgs', total_msgs
))
FROM (
  SELECT
    date(started_at, 'weekday 0', '-6 days') as week_start,
    date(started_at, 'weekday 0') as week_end,
    community_label,
    COUNT(*) as sessions,
    SUM(message_count) as total_msgs
  FROM sessions
  WHERE community_id IN ({{COMMUNITY_IDS}})
    AND started_at >= date('now', '-60 day')
  GROUP BY week_start, community_label
  ORDER BY week_start DESC, sessions DESC
)
