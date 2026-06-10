!SELECT json_group_array(json_object(
  'file', target_file,
  'sessions', sessions,
  'ops', ops,
  'tools', tools
))
FROM (
  SELECT target_file,
    COUNT(DISTINCT session_id) as sessions,
    COUNT(*) as ops,
    GROUP_CONCAT(DISTINCT tool_name) as tools
  FROM messages
  WHERE created_at >= date('now', '-1 day')
    AND tool_name IN ('Edit', 'Write', 'Read')
    AND target_file IS NOT NULL
  GROUP BY COALESCE(json_extract(file_uuids, '$[0]'), target_file)
  ORDER BY ops DESC
  LIMIT 25
)
