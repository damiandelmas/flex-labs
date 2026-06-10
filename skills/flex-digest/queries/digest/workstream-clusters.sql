!SELECT _cluster_id, _is_attractor, v.score, c.content, c.session_id, c.created_at
FROM vec_ops('similar:what are we working on, what decisions, what was built diverse peaks pool:300',
  'SELECT id FROM messages WHERE type = ''user_prompt''
   AND created_at >= date(''now'', ''-1 day'')') v
JOIN chunks c ON v.id = c.id
ORDER BY _cluster_id, _is_attractor DESC, v.score DESC
