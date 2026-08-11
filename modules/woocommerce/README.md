# WooCommerce Flex Module

WooCommerce is a public no-auth source module for the Store API product
catalogue. It captures exact `/wp-json/wc/store/v1/products` response bytes,
stores them in raw SQLite tables, and exposes read-only catalogue views.

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
`catalogue_products`, `catalogue_attributes`, `catalogue_terms`,
`catalogue_images`, and `catalogue_json_values` are SQL views over the retained
Store API objects.
