# XOLogic Flex source module

This module compiles one sealed XOLogic capture into one native SQLite Flex
cell. Exact source bytes are stored once by SHA-256; exact URL observations and
native product/specification/resource rows reference those bodies.

The artifact-only factory path writes no registry state:

```bash
python -m modules.xologic.compile.worker \
  --capture /path/to/sealed-capture \
  --cell /isolated/path/store_xologic.db \
  --store-name "Store Name" \
  --receipt /isolated/path/store_xologic.receipt.json
```

The receipt binds the sealed capture, exact module sources, Python/SQLite
environment, product fingerprint, installed `@orient`, relation populations,
and final SQLite identity. The BES acceptance profile executes the fifteen SQL
recipes directly against that artifact and writes a linked validation receipt:

```bash
python -m modules.xologic.compile.recipes \
  --cell /isolated/path/store_xologic.db \
  --build-receipt /isolated/path/store_xologic.receipt.json \
  --receipt /isolated/path/store_xologic.recipes.json
```

Registration and refresh are separate downstream operations:

```bash
FLEX_MODULE_PATH=/path/to/flex-labs/modules \
  flex init --module xologic \
  --capture /path/to/capture \
  --name bes_lighting_xologic \
  --store-name "BES Lighting"

flex search --cell bes_lighting_xologic "@orient"
flex search --cell bes_lighting_xologic \
  "SELECT title,sku,url FROM products ORDER BY title LIMIT 20"
python -m flex.refresh --cells bes_lighting_xologic --dry-run
```

The capture directory must contain `structured/products.json`, a sealed and
complete `evidence/receipt.json`, the four evidence row artifacts, and every
raw object named by a source observation. Preference, conflicts, and resolved
products are SQL projections. `product_search_documents` and `products_fts`
provide derived product-specific keyword discovery over titles, identifiers,
vendors, taxonomy, and published specifications; native relations remain the
authority for returned facts. `@orient` is the only installed saved query.
Generic chunks, embeddings, graphs, R2 publication, tenant configuration, and
Felix behavior are outside this module.
