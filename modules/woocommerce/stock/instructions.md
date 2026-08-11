# WooCommerce Cell Instructions

This cell is a raw WooCommerce Store API capture. It is not a normalized product
schema. Exact Store API response bytes and raw product JSON are authoritative.

First query:

```text
cell="woocommerce" query="@orient"
```

Use `catalogue_products` for product discovery. Join `catalogue_attributes`,
`catalogue_terms`, `catalogue_images`, or `catalogue_json_values` by
`product_id` when the question needs details. Do not infer missing attributes,
certifications, variants, or compatibility from similar products.
