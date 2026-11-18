# Slot: `modules/schema_forest/stock/presets/orient.sql`

- Change: create.
- Owns: provider-native, content-free `@orient` preset.
- Output sections: identity, capabilities, edition, five-table live shape, introspected
  query surface, honest source/container recovery, limitations.
- Implementation: use `_metadata`, provider tables, `sqlite_master`, and
  `pragma_table_info`; never advertise absent tables or generic chunk/tree semantics.
- Proof: preset output matches live row counts, five depth-zero sources, the 3,935
  physical predicate, and DDL after fixture and E2 builds.
