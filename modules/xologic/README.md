# XOLogic Flex source module

This module compiles one sealed XOLogic capture into one native SQLite Flex
cell. Exact source bytes are stored once by SHA-256; exact URL observations and
native product/specification/resource rows reference those bodies.

```bash
FLEX_MODULE_PATH=/path/to/flex-labs/modules \
  flex init --module xologic \
  --capture /path/to/capture \
  --name bes_lighting_xologic \
  --store-name "BES Lighting"

flex search --cell bes_lighting_xologic "@orient"
flex search --cell bes_lighting_xologic "@products"
python -m flex.refresh --cells bes_lighting_xologic --dry-run
```

The capture directory must contain `structured/products.json`, a sealed and
complete `evidence/receipt.json`, the four evidence row artifacts, and every
raw object named by a source observation. Preference, conflicts, and resolved
products are SQL views. Generic chunks, embeddings, R2 publication, tenant
configuration, and Felix behavior are outside this module.
