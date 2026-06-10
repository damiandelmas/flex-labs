!SELECT v.score, c.content, c.session_id, c.created_at
FROM vec_ops('similar:decisions goals accomplishments what we built what is next diverse decay:1',
  'SELECT id FROM messages WHERE type = ''user_prompt''
   AND created_at >= date(''now'', ''-1 day'')') v
JOIN chunks c ON v.id = c.id
ORDER BY v.score DESC LIMIT 10
