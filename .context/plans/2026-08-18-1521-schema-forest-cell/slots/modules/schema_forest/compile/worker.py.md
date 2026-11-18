# Slot: `modules/schema_forest/compile/worker.py`

- Change: create.
- Owns: capture resolution, manifest/source/tool verification, streaming zstd JSONL
  reader, five-table native schema, deterministic loading, staged verification, atomic
  publication, build receipt, registration helper, and standalone CLI.
- Reuse from XOLogic: canonical JSON/hash helpers, transformation hashing, staged temp
  directory, `install_cell_core`, read-only candidate verification, `os.replace`, atomic
  receipt, and registry call shape.
- Critical constraints: one-based JSONL source line ordinal is identity for
  occurrence/failure rows; retain exact UTF-8 record bytes excluding the single LF
  terminator rather than reserializing JSON; keep families unexpanded; no raw
  task/artifact loading;
  path containment; explicit statuses; no parser execution or legacy envelope tables.
- Public functions: `inspect_capture`, `build_cell`, `verify_cell`,
  `register_cell_path`, `main`. No refresh module.
- Proof: full synthetic and real-capture acceptance suite.
