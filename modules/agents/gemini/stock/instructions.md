# Gemini CLI Cell Instructions

This cell indexes Gemini CLI chat history from `~/.gemini/tmp/<projectHash>/chats/`.
It uses the shared coding-agent substrate: `chunks`, `messages`, `sessions`,
`files`, and `agent_key_chunks` mean the same thing here as in the Codex, Claude
Code, and Goose cells.

Gemini sessions are stored **raw-first**: every native record is preserved
verbatim in `_native_records` (the storage truth); the queryable substrate is a
re-derivable projection over it. Gemini-specific session metadata lives in
`_types_gemini_session` (`project_hash`, `start_time`, `summary`,
`tool_call_count`, `models_json`, `total_*_tokens`).

The cell is self-describing. Start here:

```text
cell="gemini" query="@orient"
```

`@orient` returns the schema, presets, graph entry points, and examples. Every
Flex query must be valid SQL or a preset.

## Core Surfaces

`chunks` — unified retrieval surface. Types: `user_prompt`, `assistant`,
`tool_call`. Tool calls (`read_file`, `write_file`, `replace`,
`run_shell_command`, `grep_search`, …) are mapped to canonical CC names
(`Read`, `Write`, `Edit`, `Bash`, `Grep`, …).

`messages` — ordered event surface. `messages.file_body` holds full file
captures from `write_file`/`replace` (args) and `read_file` (result).

`_native_records` — lossless native JSON, one row per session and per message.
Query provider-specific fields not in the projection with `json_extract`, e.g.
`SELECT json_extract(payload_json,'$.tokens.thoughts') FROM _native_records
WHERE record_type='message'`.

## Notes

- `info` messages (CLI update notices) are preserved in `_native_records` but
  not projected into chunks — they are noise for search.
- `thoughts` (model reasoning) are stored in `_raw_content` against the
  assistant chunk and are full-text searchable.
