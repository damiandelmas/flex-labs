!WITH alltime_hubs AS MATERIALIZED (
  SELECT session_id, community_id, community_label, fingerprint_index, started_at
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY community_id ORDER BY centrality DESC) as rn
    FROM sessions
    WHERE is_hub = 1
      AND community_id IN ({{COMMUNITY_IDS}})
      AND community_label IS NOT NULL
  )
  WHERE rn = 1
),
recent_hubs AS MATERIALIZED (
  -- Most substantial sessions INSIDE the seed window, so descriptions reflect
  -- what the community did recently, not just its all-time peak.
  SELECT session_id, community_id, community_label, fingerprint_index, started_at
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY community_id ORDER BY centrality DESC, message_count DESC) as rn
    FROM sessions
    WHERE community_id IN ({{COMMUNITY_IDS}})
      AND community_label IS NOT NULL
      AND started_at >= date('now', '-{{DAYS}} days')
      AND message_count >= 10
      AND session_id NOT IN (SELECT session_id FROM alltime_hubs)
  )
  WHERE rn <= 2
),
top_hubs AS MATERIALIZED (
  SELECT *, 'all-time' as tier FROM alltime_hubs
  UNION ALL
  SELECT *, 'recent' as tier FROM recent_hubs
),
first_prompts AS (
  SELECT h.community_label, h.session_id, h.tier, h.started_at,
    SUBSTR(COALESCE(h.fingerprint_index, ''), 1, 1500) as fingerprint,
    SUBSTR(m.content, 1, 300) as opener,
    ROW_NUMBER() OVER (PARTITION BY h.session_id ORDER BY m.position) as msg_rn
  FROM top_hubs h
  JOIN messages m ON m.session_id = h.session_id
    AND m.type = 'user_prompt'
)
SELECT json_group_array(json_object(
  'community', community_label,
  'session_id', session_id,
  'tier', tier,
  'started', started_at,
  'fingerprint', fingerprint,
  'opener', opener
))
FROM first_prompts
WHERE msg_rn = 1
