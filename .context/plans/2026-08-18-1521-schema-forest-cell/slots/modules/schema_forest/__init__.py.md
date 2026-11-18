# Slot: `modules/schema_forest/__init__.py`

- Change: create.
- Owns: package identity and `MODULE` re-export only.
- Contract: mirror the minimal XOLogic package seam; import `MODULE` from `install.py`
  and expose no compiler behavior.
- Proof: module imports through external Flex module discovery.

