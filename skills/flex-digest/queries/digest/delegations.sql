!SELECT json_group_array(json_object(
  'parent', e.parent_source_id,
  'child', e.child_session_id,
  'agent_type', e.agent_type,
  'child_title', SUBSTR(s.title, 1, 80),
  'child_msgs', s.message_count
))
FROM _edges_delegations e
LEFT JOIN sessions s ON e.child_session_id = s.session_id
WHERE e.chunk_id IN (
  SELECT id FROM messages WHERE created_at >= date('now', '-1 day')
)
