# XOLogic Flex source module

This module compiles one sealed XOLogic capture into one native SQLite Flex
cell. Exact source bytes remain in the sealed capture; the cell contains their
source pointers and hashes beside native product, specification, link, and
resource relations.

The artifact-only factory path writes no registry state:

```bash
python -m modules.xologic.compile.worker \
  --capture /path/to/sealed-capture \
  --cell /isolated/path/store_xologic.db \
  --store-name "Store Name" \
  --receipt /isolated/path/store_xologic.receipt.json
```

The compact receipt identifies the sealed capture, transformation bytes, final
SQLite, installed `@orient`, instruction artifact, and relation populations.

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
products are SQL projections. The provider-named `product_specification`,
`additional_product_details`, `other_details`, `images`, `downloads`, `options`,
`additional_options_that_would_change_other_selections`, `collection_products`,
and `related_products` views expose the visible source surfaces without replacing
their exact occurrences and link relations. `product_search_documents` and `products_fts`
provide derived product-specific keyword discovery over titles, identifiers,
vendors, taxonomy, and published specifications; native relations remain the
authority for returned facts. `@orient` is the only installed saved query and
contains only the cell's schema and query contract; retrieval methods and SQL
recipes live in `stock/instructions.md`.
Generic chunks, embeddings, graphs, R2 publication, tenant configuration, and
Felix behavior are outside this module.
