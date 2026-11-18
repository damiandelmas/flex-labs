# Slot: `modules/schema_forest/install.py`

- Change: create.
- Owns: `CLI_NAME = "schema-forest"`, `MODULE` declaration, CLI arguments, install run.
- Inputs: required `--capture`, optional `--name`.
- Behavior: publish DB to `<capture>/.derived/<name>.db`, receipt to
  `<capture>/.receipts/`, then register only after verification with
  `lifecycle='static'` and `allow_external=True`.
- Module spec: `cell_type = schema_forest`, static lifecycle, provider-owned preset and
  instructions, no generic views/retrieval profile.
- Proof: external module installation/discovery and static registration tests.
