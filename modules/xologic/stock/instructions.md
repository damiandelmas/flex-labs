# XOLogic catalogue query method

The cell's `@orient` contract has already been resolved. Write SQL against the
relations and columns it advertises; do not assume a generic `chunks` relation.
Shared lighting instructions own lighting expertise, conversation behavior,
claim safety, and response presentation. This section only explains the XOLogic
query surface.

Start with the one-row resolved product projection for ordinary discovery,
filtering, comparison, and display. Use the long-form resolved specification
relation for fields not promoted into that projection. Inspect native
specification occurrences and conflicts when a value is repeated, ambiguous,
conflicting, or consequential. Native rows remain the evidence; resolved fields
are deterministic SQL interpretations.

Evaluate category and subcategory together. A product under a parent category
may still be an accessory, kit, stem, mount, replacement part, outlet, bulb,
lamp, or other component. Exclude those populations only when the user requests
complete products, and retain them when explicitly requested.

Combine mandatory requirements conjunctively when their absence disqualifies a
product. A mandatory concept is a `WHERE` or `EXISTS` condition; relevance cannot
compensate for its absence. Keep optional preferences in ranking logic. Never
return near-matches as matches or silently relax requirements. When useful,
return nearby candidates separately and state which requested evidence they do
not explicitly publish. Treat `NULL` as unpublished or unresolved, never as
affirmative or negative evidence.

Use `products_fts` to discover a broad product population from titles, SKUs,
part numbers, vendors, taxonomy, and published specifications. Join through
`product_search_documents.product_ref` to `resolved_products`, then apply
structured requirements there. FTS relevance finds candidates; it does not
establish that a product satisfies a mandatory specification.

Every product returned across the tool boundary must be a complete row from
`product_dossiers`. Search, filter, rank, and count with narrower relations
inside CTEs, retain the resulting `product_ref` values, and join those identities
to `product_dossiers` for the final product result. Do not return hand-picked
product columns: a partial product row invites unsupported inference about
omitted fields. Dossiers include every distinct published specification and
customer-usable relationship with occurrence counts. Primary product images and
published documents are included; crawl-only pixels, site icons, raw provider
JSON, HTML, and physical source locators remain available in native relations
instead of being repeated in every model payload. Aggregate counts and
vocabulary inspection are not product results and do not require dossier
expansion.

The compact dossier arrays use these fixed positions:
`taxonomy_json` is `[depth,label]`; `specifications_json` is
`[source_layer,label,value,visible,occurrence_count]`; `conflicts_json` is
`[label,distinct_values,values_seen]`; `variants_json` is
`[kind,url,occurrence_count]`; and `documents_json` is
`[kind,url,occurrence_count]`.

Return at most five complete products in one batch and keep the sum of their
`length(product_dossier_payloads.dossier_json)` values at or below 30000
characters. Always permit the first complete product even if it alone exceeds
that target. If more eligible products exist, state that more are available
without claiming a total unless the same predicate was counted. Use another
complete batch for additional products; never replace complete dossiers with a
larger set of partial rows. Flex gates ordinary results above roughly 35000
characters, so crossing this boundary returns a window envelope rather than
product rows.

Construct an FTS expression from useful natural-language search terms rather
than passing the customer's sentence through as query syntax. Do not use FTS
for a SKU, part number, GTIN, or handle: query `product_identifiers` by its
normalized value, retain every matching product identity, and expand those
identities through complete dossiers.

When a request contains a likely catalogue identifier, isolate that identifier
from the surrounding words and make `product_identifiers` the first lookup. A
standalone four-or-more-digit token such as `43999`, or an alphanumeric code
such as `P400108-31M`, is a likely identifier. Search that token alone; do not
also require the customer's vendor, title, or product-type wording to match.
Those words help describe the request but may differ from the provider's title.
If the identifier lookup returns products, use those identities rather than
starting over with FTS.

XOLogic style and finish values may contain several published labels. When the
user requests one member such as farmhouse or black, use case-insensitive
membership or containment rather than whole-string equality unless the exact
literal value itself is required.

Use safely parsed numeric ranges only when parse status permits it. Keep fixture
properties separate from bulb, lamp, driver, and component properties; bulb
dimmability does not prove fixture dimmability. Preserve raw source values when
notation or interpretation matters.

Use taxonomy relations for the provider's complete category path, product-link
relations for published variants and options, and product-resource relations for
images and documents. Resolve finish and family questions through actual linked
or matching product SKUs rather than title similarity. Return exact SKUs, option
values, and URLs from SQL.

Include the identifying columns and exact product URL needed to ground the
answer. Copy URLs verbatim: never shorten, reconstruct, normalize, or infer one.
Compute totals with the same predicates used for the eligible population; a
limited result page never proves a total.

In order to find a product by SKU, part number, GTIN, or handle, normalize the
customer's identifier and query `product_identifiers` directly. A partial
identifier such as `43999` may legitimately identify several published product
options, so retain every match before applying the complete-product batch. The
identifier relation establishes identity; each complete dossier supplies the
facts needed to answer.

```sql
WITH discovered AS (
  SELECT DISTINCT product_ref
  FROM product_identifiers
  WHERE normalized_identifier LIKE '%'||lower(trim(:identifier))||'%'
), ranked AS (
  SELECT x.*,
         row_number() OVER (ORDER BY x.product_ref) AS batch_rank
  FROM discovered x
), sized AS (
  SELECT x.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY x.batch_rank
         ) AS cumulative_chars
  FROM ranked x
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT dossier.*
FROM sized x
JOIN product_dossiers dossier USING(product_ref)
WHERE x.batch_rank<=5
  AND (x.batch_rank=1 OR x.cumulative_chars<=30000)
ORDER BY dossier.title,dossier.sku;
```

In order to find products matching several mandatory specifications when their
absence disqualifies a recommendation, retrieve plausible candidates and apply
every required condition to the same eligible population. This treats the
requirements conjunctively while allowing FTS to recognize vocabulary across
several source fields. An empty result establishes only that no product satisfies
this exact combination of published specifications.

```sql
WITH discovered AS (
  SELECT d.product_ref,
         bm25(products_fts,0.0,8.0,12.0,4.0,5.0,2.0,1.0) AS text_rank
  FROM products_fts
  JOIN product_search_documents d ON d.rowid=products_fts.rowid
  WHERE products_fts MATCH :terms
), eligible AS (
  SELECT x.product_ref,x.text_rank,p.price_min,p.title
  FROM discovered x
  JOIN resolved_products p USING(product_ref)
  WHERE p.price_min<=:max_price
    AND lower(COALESCE(p.finish,'')) LIKE '%'||lower(:required_finish)||'%'
    AND lower(COALESCE(p.style,'')) LIKE '%'||lower(:required_style)||'%'
    AND EXISTS (
      SELECT 1 FROM taxonomy_paths t
      WHERE t.product_ref=p.product_ref
        AND lower(t.label) LIKE lower(:required_type)||'%'
    )
    AND NOT EXISTS (
      SELECT 1 FROM taxonomy_paths t
      WHERE t.product_ref=p.product_ref
        AND (
          lower(t.label) LIKE '%accessor%'
          OR lower(t.label) LIKE '%replacement%'
          OR lower(t.label) LIKE '%part%'
        )
    )
), ranked AS (
  SELECT e.*,
         row_number() OVER (
           ORDER BY e.text_rank,e.price_min,e.title,e.product_ref
         ) AS batch_rank
  FROM eligible e
), sized AS (
  SELECT e.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY e.batch_rank
         ) AS cumulative_chars
  FROM ranked e
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.text_rank,e.price_min,e.title;
```

In order to prefer a quality such as style when it is not required, rank that
quality only after mandatory eligibility has been established. A preferred
quality may reorder eligible products but may not admit an otherwise ineligible
one. If the user makes that quality mandatory, move it into the eligible
predicate instead.

```sql
WITH eligible AS (
  SELECT p.product_ref,p.price_min,p.title,p.style
  FROM resolved_products p
  WHERE p.price_min<=:max_price
    AND lower(COALESCE(p.finish,'')) LIKE '%'||lower(:required_finish)||'%'
    AND EXISTS (
      SELECT 1 FROM taxonomy_paths t
      WHERE t.product_ref=p.product_ref
        AND lower(t.label) LIKE lower(:required_type)||'%'
    )
), ranked AS (
  SELECT e.*,
         row_number() OVER (
           ORDER BY
             CASE WHEN lower(COALESCE(e.style,''))
                       LIKE '%'||lower(:preferred_style)||'%' THEN 0 ELSE 1 END,
             e.price_min,e.title,e.product_ref
         ) AS batch_rank
  FROM eligible e
), sized AS (
  SELECT e.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY e.batch_rank
         ) AS cumulative_chars
  FROM ranked e
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

Use only fields published by the resolved contract. Compute a count from the
same eligible predicate before making a total-inventory claim.
