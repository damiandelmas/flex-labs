# WooCommerce Flex Module

WooCommerce is a public no-auth source module for the Store API product
catalogue. It captures exact `/wp-json/wc/store/v1/products` response bytes,
stores them in raw SQLite tables, then applies the `lighting` profile in that
same cell. Native evidence remains authoritative; every search or dossier
surface is deterministic and rebuildable.

The boundaries are explicit:

```text
compile/                              Store API -> source-faithful SQLite
stock/                                provider-common WooCommerce views/presets
profiles/lighting/stock/sql/          lighting projections and search surfaces
profiles/lighting/stock/presets/      the WooCommerce-lighting @orient
profiles/lighting/stock/instructions.md  the WooCommerce-lighting query method
```

There is no generic commerce instruction contract. `woocommerce-lighting` is
the only implemented profile; another profile should be added only for a real
non-lighting consumer.

## Install

```bash
flex core init --module woocommerce --store-url https://example.com --name woocommerce
```

Useful flags:

```text
--store-url https://store.example     Public WooCommerce storefront URL
--name mystore_woo                    Cell name
--per-page 50                         Store API page size, 1-100
--retries 2                           Transient HTTP retries
```

## Query

Start with:

```bash
flex core search --cell woocommerce "@orient"
flex core search --cell woocommerce "@scope"
flex core search --cell woocommerce "@products"
```

Raw JSON and response bytes are authoritative. Product rows in
`resolved_products` provide conservative typed helpers; `spec_occurrences`
retains every exact source path, label, and value behind those helpers. Stable
identity lives in `product_identifiers`, broad retrieval in
`product_search_documents` and FTS5 `products_fts`, and complete model-facing
results in `product_dossiers` plus `product_dossier_payloads`. Raw
`catalogue_*` views remain available over the retained Store API objects.

`@orient` is the live map of the store, its public relations, and the evidence
boundary behind them. It contains no worked SQL or response behavior. The
profile's `profiles/lighting/stock/instructions.md` owns the WooCommerce-lighting
retrieval method and its worked query recipes.

Price exposure follows `price_visibility`. Stock, in-stock, backorder, and
purchasability remain separate fields. Multi-valued attributes/options and
conflicting specification occurrences remain intact in complete dossiers.

Rebuild a compiled projection from its retained sealed response bytes without
calling the storefront again:

```bash
python -m modules.woocommerce.compile.worker --source-cell mystore_woo --cell mystore_woo
```

## Batch Build

For controlled bulk ingestion, pass a JSON manifest array to the batch runner:

```json
[
  {"cell": "store_one_woo", "url": "https://store-one.example", "description": "Store One"},
  {"cell": "store_two_woo", "url": "https://store-two.example", "price_policy": "login_required"}
]
```

```bash
python -m modules.woocommerce.compile.batch --manifest stores.json --concurrency 2
```

Each row runs the same native single-store build and registration path as the
regular worker. Duplicate cells or URLs fail validation before any source is
fetched. `price_policy` is `public`, `login_required`, or `hidden`; it may be set
per manifest row, while `--price-policy` supplies the batch default.
