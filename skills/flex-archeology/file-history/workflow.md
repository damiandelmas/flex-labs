# File History

`$ARGUMENTS` is the file path to trace.

---

## What's stored

Two sources of full file content in `_raw_content`:

| `tool_name` | What it is | How it got there |
|---|---|---|
| `Write` | Full file JSON from Write tool calls | Worker captures tool input |
| `_file_backup` | Pre-edit file snapshot (full content before any Edit) | Worker reads file-history snapshots at sync time |
| `Edit` | Diff payload only (`old_string` / `new_string`) | Worker captures tool input |

Both Write and `_file_backup` give full recoverable file states. Together they cover every version boundary.

---

## Step 1 — Get all recoverable versions

One query. Returns every full snapshot (Write content + pre-edit backups) for the file:

```sql
SELECT erc.chunk_id, m.session_id, m.timestamp, m.position,
  rc.tool_name, rc.byte_length, substr(rc.content, 1, 100) as preview
FROM _edges_raw_content erc
JOIN _raw_content rc ON erc.content_hash = rc.hash
JOIN messages m ON erc.chunk_id = m.id
WHERE m.target_file = '$FILE_PATH'
  AND rc.tool_name IN ('Write', '_file_backup')
ORDER BY m.timestamp, m.session_id, m.position
```

Cell: `claude_code`

Each row is a recoverable version. `_file_backup` rows are pre-edit states. `Write` rows are post-write states.

## Step 2 — Present version table

Format as:

```
| Ver | Time | Session | chunk_id | Source | Size | First line |
|---|---|---|---|---|---|---|
| 1 | 2026-04-20 12:03 | abc123 | ...._785 | Write | 19KB | "# Flex: Composable Semantic..." |
| 2 | 2026-04-20 12:11 | abc123 | ...._825 | _file_backup | 18KB | "# Flex: Composable Semantic..." (pre-edit) |
| 3 | 2026-04-21 08:44 | def456 | ...._1073 | Write | 19KB | "# FlexVec: Composable Modul..." |
```

Group by session if multiple sessions touched the file.

## Step 3 — Recover a specific version

```sql
SELECT rc.content
FROM _edges_raw_content erc
JOIN _raw_content rc ON erc.content_hash = rc.hash
WHERE erc.chunk_id = '$CHUNK_ID'
  AND rc.tool_name IN ('Write', '_file_backup')
```

- **Write** content is JSON. Extract file body: `json.loads(content)["content"]`
- **_file_backup** content is the raw file text. No parsing needed.

## Step 4 — Diff two versions

Recover two versions via Step 3. Write to temp files:

```bash
diff --unified /tmp/v_old.md /tmp/v_new.md
```

## Step 5 — Cross-session provenance

```sql
SELECT m.session_id, s.title,
  MIN(datetime(m.timestamp, 'unixepoch', '-7 hours')) as first_touch,
  MAX(datetime(m.timestamp, 'unixepoch', '-7 hours')) as last_touch,
  COUNT(*) as ops
FROM messages m
JOIN sessions s ON m.session_id = s.session_id
WHERE m.target_file = '$FILE_PATH'
  AND m.tool_name IN ('Write', 'Edit')
GROUP BY m.session_id
ORDER BY first_touch
```

---

## Notes

- `_file_backup` is raw file text. `Write` is JSON with a `content` field. Handle both.
- One `_file_backup` snapshot can cover multiple files edited in the same tool call — the chunk_id links to the snapshot event, not a specific file. Filter by checking `preview` / `content` if the snapshot chunk covers multiple files.
- Content hashes deduplicate: identical content across versions = one `_raw_content` row.
- Timestamps are Unix epoch UTC. Display as `datetime(ts, 'unixepoch', '-7 hours')` — adjust the UTC offset for your timezone.
