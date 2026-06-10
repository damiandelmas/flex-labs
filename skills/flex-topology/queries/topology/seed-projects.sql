!SELECT json_group_array(json_object(
  'project', project,
  'sessions', sessions,
  'total_msgs', total_msgs,
  'first_seen', first_seen,
  'last_seen', last_seen,
  'days_active', CAST(JULIANDAY(last_seen) - JULIANDAY(first_seen) AS INTEGER),
  'days_since_last', CAST(JULIANDAY('now') - JULIANDAY(last_seen) AS INTEGER)
))
FROM (
  SELECT project,
    COUNT(*) as sessions,
    SUM(message_count) as total_msgs,
    MIN(started_at) as first_seen,
    MAX(started_at) as last_seen
  FROM sessions
  WHERE community_id IN ({{COMMUNITY_IDS}})
    AND project IS NOT NULL
  GROUP BY project
  ORDER BY sessions DESC
  LIMIT 25
)
