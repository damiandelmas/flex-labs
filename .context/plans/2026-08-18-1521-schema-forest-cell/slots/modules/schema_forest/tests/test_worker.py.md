# Slot: `modules/schema_forest/tests/test_worker.py`

- Change: create.
- Owns: runtime-built sealed fixture, success/failure compiler tests, static external
  registration proof, synthetic deterministic rebuild proof, and opt-in real E2 capture
  acceptance test.
- Mandatory failures: missing/unsupported manifest, hash/count mismatch, malformed
  JSONL, duplicate schema signature, dangling signature/family member, source tamper,
  path escape, and publication failure preserving last-good bytes.
- Mandatory successes: one-based line ordinals, non-unique occurrence/failure IDs,
  exact source JSON excluding its LF terminator, family
  multiplicity, explicit statuses, source recovery hash, introspective `@orient`,
  logical determinism, static external registration, and forbidden-table absence.
- Real proof: 45,101 occurrences, 827 schemas, 827 families, 21 failures, 10,418
  distinct producer occurrence IDs, 3,935 physical predicate, and five verified roots.
