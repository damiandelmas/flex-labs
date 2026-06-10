!SELECT json_group_array(json_object(
  'id', session_id,
  'title', SUBSTR(title, 1, 100),
  'msgs', message_count,
  'ended', ended_at,
  'overnight', CASE WHEN DATE(started_at) != DATE(ended_at) THEN 1 ELSE 0 END
))
FROM sessions
WHERE started_at >= date('now', '-1 day')
  AND session_id NOT LIKE 'agent-%'
  AND (DATE(started_at) != DATE(ended_at) OR message_count > 100)
