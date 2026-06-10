!WITH top_hubs AS (
  SELECT session_id, community_id, community_label, fingerprint_index
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY community_id ORDER BY centrality DESC) as rn
    FROM sessions
    WHERE is_hub = 1
      AND community_id IN ({{COMMUNITY_IDS}})
  )
  WHERE rn <= 2
),
first_prompts AS (
  SELECT h.community_label, h.session_id,
    COALESCE(h.fingerprint_index, '') as fingerprint,
    SUBSTR(m.content, 1, 300) as opener,
    ROW_NUMBER() OVER (PARTITION BY h.session_id ORDER BY m.position) as msg_rn
  FROM top_hubs h
  JOIN messages m ON m.session_id = h.session_id
    AND m.type = 'user_prompt'
)
SELECT json_group_array(json_object(
  'community', community_label,
  'session_id', session_id,
  'fingerprint', fingerprint,
  'opener', opener
))
FROM first_prompts
WHERE msg_rn = 1
