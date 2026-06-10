!SELECT json_object(
  'sessions', (
    SELECT json_group_array(json_object(
      'id', s.session_id,
      'title', SUBSTR(s.title, 1, 120),
      'msgs', s.message_count,
      'start', s.started_at,
      'end', s.ended_at,
      'community', s.community_label,
      'centrality', ROUND(s.centrality, 6),
      'hub', s.is_hub,
      'bridge', s.is_bridge,
      'fingerprint', s.fingerprint_index
    ))
    FROM sessions s
    WHERE s.started_at >= date('now', '-1 day')
      AND s.session_id NOT LIKE 'agent-%'
    ORDER BY s.started_at ASC
  ),
  'projects', (
    SELECT json_group_array(json_object(
      'project', project,
      'sessions', cnt,
      'total_msgs', total_msgs
    ))
    FROM (
      SELECT project, COUNT(*) as cnt, SUM(message_count) as total_msgs
      FROM sessions
      WHERE started_at >= date('now', '-1 day')
        AND session_id NOT LIKE 'agent-%'
      GROUP BY project
      ORDER BY cnt DESC
    )
  )
) as inventory
