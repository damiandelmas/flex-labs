# WooCommerce lighting catalogue query method

This section explains how Felix queries the WooCommerce lighting catalogue.
Shared lighting instructions own qualification, recommendation, conversation,
and response presentation.

The Store API evidence is authoritative. `resolved_products` is a convenient
one-row interpretation; `resolved_spec_values`, `spec_occurrences`, and
`spec_conflicts` retain the exact published evidence. Use the resolved row for
ordinary discovery and filtering, then confirm strict or consequential claims
against the occurrence relations. A scalar convenience field may contain one
preferred value while the occurrence and dossier arrays preserve several
published finishes, certifications, options, or other values.

## Retrieval contract

Use `product_identifiers` first for a likely native product ID, SKU, slug, or
complete product URL. Normalize only with `lower(trim(...))`; retain every
matching `product_ref`. Do not use FTS to establish identifier identity.

Use `products_fts` for broad discovery across titles, identifiers, brand labels,
taxonomy, attributes, descriptions, and published specifications. FTS establishes
candidate recall, not technical eligibility. SQLite FTS5 requires the actual
virtual-table name as the left operand of `MATCH` and argument to `bm25()`; do
not alias `products_fts`.

Apply mandatory requirements conjunctively to the same product. A mandatory
concept belongs in `WHERE` or `EXISTS`; relevance cannot compensate for its
absence. Optional preferences belong in ranking. Never silently relax a
requirement or present a nearby result as an exact match. `NULL` means
unpublished or unresolved, never false.

Every returned product must be a complete row from `product_dossiers`. Search,
filter, rank, and count with narrower relations inside CTEs, retain
`product_ref`, then join to `product_dossiers` for the final rows. Aggregate
counts and vocabulary inspection are not product results and do not require
dossier expansion.

Return at most five complete products per batch. Join
`product_dossier_payloads` while ranking and keep cumulative
`length(dossier_json)` at or below 30000 characters, always allowing the first
product. Include `total_matches`, `returned`, and `has_more`. If more are
available, continue with an exclusion over already returned `product_ref`
values; never substitute a larger set of partial rows.

Copy `product_url` exactly. It is a customer link, not product identity. Never
construct, shorten, repair, or normalize it.

## WooCommerce commerce semantics

- `price_visibility='public'` authorizes the masked dossier price fields.
  `login_required` and `hidden` never authorize recovering price from raw
  tables. Currency-like text embedded in a public description is product prose,
  not an authorized current price when visibility is non-public. A non-equal
  `price_min` and `price_max` is a range, not one exact price.
- `stock_status`, `is_in_stock`, `is_on_backorder`, and
  `is_purchasable` are independent source facts. Do not convert blank or NULL
  into “in stock,” and do not claim purchasability merely from stock status.
- A variable parent product may publish option attributes and variation IDs.
  Those IDs do not contain independent variant price, stock, or specifications
  in this capture. Do not present them as independent products or inherit the
  parent's commerce fields onto each option.
- `brands` is a published WooCommerce brand label. It is searchable evidence,
  not proof of manufacturer identity.
- Image names and alt text are not in the FTS document and never establish
  technical properties.

When a tool call returns a retryable SQL error, preserve all customer
constraints, fix only the SQL, and retry once. Do not broaden the request to
make an invalid query return rows.

## Complete product batch pattern

Use this final expansion after an `eligible(product_ref, rank_score)` CTE:

```sql
, ranked AS (
  SELECT e.product_ref,
         row_number() OVER (
           ORDER BY e.rank_score,e.product_ref
         ) AS batch_rank
  FROM eligible e
), sized AS (
  SELECT r.*,
         sum(length(p.dossier_json)) OVER (
           ORDER BY r.batch_rank
         ) AS cumulative_chars
  FROM ranked r
  JOIN product_dossier_payloads p USING(product_ref)
), batch AS (
  SELECT *
  FROM sized
  WHERE batch_rank<=5
    AND (batch_rank=1 OR cumulative_chars<=30000)
)
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       d.*
FROM batch b
JOIN product_dossiers d USING(product_ref)
ORDER BY b.batch_rank;
```

The recipes below preserve that final expansion while varying candidate and
eligibility logic.

## 1. Resolve an identifier before descriptive search

> “Show me SKU E2-HB-240.”

```sql
WITH wanted AS (
  SELECT lower(trim(:identifier)) AS identifier
), eligible AS (
  SELECT DISTINCT i.product_ref,0 AS rank_score
  FROM product_identifiers i
  CROSS JOIN wanted w
  WHERE i.normalized_identifier=w.identifier
     OR i.normalized_identifier LIKE w.identifier||'%'
),
ranked AS (
  SELECT e.product_ref,row_number() OVER (ORDER BY e.rank_score,e.product_ref) batch_rank
  FROM eligible e
),
sized AS (
  SELECT r.*,sum(length(p.dossier_json)) OVER (ORDER BY r.batch_rank) cumulative_chars
  FROM ranked r JOIN product_dossier_payloads p USING(product_ref)
),
batch AS (
  SELECT * FROM sized WHERE batch_rank<=5
    AND (batch_rank=1 OR cumulative_chars<=30000)
)
SELECT (SELECT count(*) FROM ranked) total_matches,count(*) OVER () returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () has_more,d.*
FROM batch b JOIN product_dossiers d USING(product_ref)
ORDER BY b.batch_rank;
```

A standalone four-or-more-digit token or alphanumeric catalogue code is a likely
identifier. Isolate it from surrounding prose before lookup.

## 2. Run broad descriptive product discovery

> “Show me linear high bays.”

```sql
WITH discovered AS (
  SELECT product_ref,bm25(products_fts,0.0,4.0,1.5,1.0,1.5,1.0,0.5) AS score
  FROM products_fts
  WHERE products_fts MATCH :terms
), eligible AS (
  SELECT d.product_ref,d.score AS rank_score
  FROM discovered d
  JOIN resolved_products r USING(product_ref)
  WHERE lower(COALESCE(r.title,'')) LIKE '%high bay%'
     OR lower(COALESCE(r.categories,'')) LIKE '%high bay%'
     OR EXISTS (
       SELECT 1 FROM spec_occurrences s
       WHERE s.product_id=r.product_ref
         AND lower(s.value) LIKE '%high bay%'
     )
),
ranked AS (
  SELECT e.product_ref,row_number() OVER (ORDER BY e.rank_score,e.product_ref) batch_rank
  FROM eligible e
),
sized AS (
  SELECT r.*,sum(length(p.dossier_json)) OVER (ORDER BY r.batch_rank) cumulative_chars
  FROM ranked r JOIN product_dossier_payloads p USING(product_ref)
),
batch AS (
  SELECT * FROM sized WHERE batch_rank<=5
    AND (batch_rank=1 OR cumulative_chars<=30000)
)
SELECT (SELECT count(*) FROM ranked) total_matches,count(*) OVER () returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () has_more,d.*
FROM batch b JOIN product_dossiers d USING(product_ref)
ORDER BY b.batch_rank;
```

Construct `:terms` from useful catalogue words, not the customer's sentence as
raw FTS syntax.

## 3. Apply ordinary must-have filters

> “Show black wall lights under $300.”

After broad discovery, apply all mandatory predicates on one resolved row.
Membership in a multi-valued field is safest through occurrences.

```sql
WITH discovered AS (
  SELECT product_ref,bm25(products_fts) score
  FROM products_fts WHERE products_fts MATCH :terms
), eligible AS (
  SELECT DISTINCT d.product_ref,d.score AS rank_score
  FROM discovered d JOIN resolved_products r USING(product_ref)
  WHERE r.price_visibility='public'
    AND r.price_min<=300
    AND EXISTS (
      SELECT 1 FROM resolved_spec_values s
      WHERE s.product_id=r.product_ref
        AND s.canonical_field='finish'
        AND lower(s.raw_value) LIKE '%black%'
    )
    AND (lower(r.title) LIKE '%wall%' OR lower(r.categories) LIKE '%wall%')
)
SELECT product_ref,rank_score FROM eligible;
```

Use the complete product batch pattern for the final result.

## 4. Enforce exclusions without weakening alternatives

> “Black or bronze sconces under $300, but not integrated LED.”

Express allowed alternatives with `OR` inside one mandatory group and exclusions
with `NOT EXISTS` or explicit negative predicates.

```sql
SELECT DISTINCT r.product_ref
FROM resolved_products r
WHERE r.price_visibility='public' AND r.price_min<=300
  AND (lower(r.title) LIKE '%sconce%' OR lower(r.categories) LIKE '%sconce%')
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref AND s.canonical_field='finish'
      AND (lower(s.raw_value) LIKE '%black%' OR lower(s.raw_value) LIKE '%bronze%')
  )
  AND NOT EXISTS (
    SELECT 1 FROM spec_occurrences s
    WHERE s.product_id=r.product_ref
      AND lower(s.value) LIKE '%integrated led%'
  );
```

## 5. Require several published specifications together

> “Find 5000K, 277V, at least 30,000 lumens, 0–10V dimming, DLC-listed high bays.”

Use typed numeric helpers only where parse status is usable. Use occurrence
membership for certifications and other multi-valued text.

```sql
SELECT DISTINCT r.product_ref
FROM resolved_products r
WHERE (lower(r.title) LIKE '%high bay%' OR lower(r.categories) LIKE '%high bay%')
  AND r.cct_min<=5000 AND r.cct_max>=5000
  AND r.voltage_min<=277 AND r.voltage_max>=277
  AND r.lumens_max>=30000
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref
      AND s.canonical_field='dimming_protocols'
      AND replace(lower(s.raw_value),' ','') LIKE '%0-10v%'
  )
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref
      AND s.canonical_field='safety_rating'
      AND lower(s.raw_value) LIKE '%dlc%'
  );
```

Do not rely on `resolved_products.safety_rating` alone: a product may publish
several certifications and the scalar helper is not exhaustive.

## 6. Apply trusted numeric limits

> “Outdoor lights under $300 with at least 1,000 lumens that support 120V.”

```sql
SELECT DISTINCT r.product_ref
FROM resolved_products r
WHERE r.price_visibility='public' AND r.price_min<=300
  AND r.lumens_max>=1000
  AND r.voltage_min<=120 AND r.voltage_max>=120
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref
      AND s.canonical_field='location_rating'
      AND (lower(s.raw_value) LIKE '%outdoor%'
        OR lower(s.raw_value) LIKE '%wet%'
        OR lower(s.raw_value) LIKE '%damp%')
  );
```

A selectable min/max range supports only published choices. A continuous range
may support intermediate values. Preserve the exact occurrence in the dossier.

## 7. Count and display the same eligible population

Build one `eligible` CTE and use it for both total and rows. Never infer a total
from a limited page.

```sql
WITH eligible AS (
  SELECT DISTINCT r.product_ref
  FROM resolved_products r
  WHERE r.price_visibility='public' AND r.price_min<=200
    AND lower(r.categories) LIKE '%vanity%'
    AND EXISTS (
      SELECT 1 FROM resolved_spec_values s
      WHERE s.product_id=r.product_ref
        AND s.canonical_field='finish'
        AND lower(s.raw_value) LIKE '%black%'
    )
)
SELECT count(*) AS total_matches FROM eligible;
```

Use the identical `eligible` definition in the complete batch query.

## 8. Treat a preference as a preference

> “Show brass pendants under $600; modern would be ideal.”

Keep brass, pendant, and price as eligibility. Rank modern higher.

```sql
SELECT DISTINCT r.product_ref,
  CASE WHEN EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref AND s.canonical_field='style'
      AND lower(s.raw_value) LIKE '%modern%'
  ) THEN 0 ELSE 1 END AS rank_score
FROM resolved_products r
WHERE r.price_visibility='public' AND r.price_min<=600
  AND lower(r.categories) LIKE '%pendant%'
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref AND s.canonical_field='finish'
      AND lower(s.raw_value) LIKE '%brass%'
  );
```

## 9. Separate exact results from nearby alternatives

Run the exact predicate unchanged. Then run a second explicitly relaxed query
only if useful. Label which condition the alternative lacks; never merge both
populations under one “matches” heading.

```sql
WITH candidates AS (
  SELECT r.product_ref,
    EXISTS (
      SELECT 1 FROM resolved_spec_values s
      WHERE s.product_id=r.product_ref AND s.canonical_field='style'
        AND lower(s.raw_value) LIKE '%traditional%'
    ) AS exact_style,
    EXISTS (
      SELECT 1 FROM resolved_spec_values s
      WHERE s.product_id=r.product_ref AND s.canonical_field='style'
        AND lower(s.raw_value) LIKE '%transitional%'
    ) AS nearby_style
  FROM resolved_products r
  WHERE lower(r.categories) LIKE '%vanity%'
)
SELECT product_ref,
       CASE WHEN exact_style THEN 'exact' ELSE 'alternative' END result_kind
FROM candidates
WHERE exact_style OR nearby_style;
```

## 10. Inspect published options without inventing variants

> “What finishes can I choose on this fixture?”

```sql
SELECT attribute_position,attribute_name,has_variations,
       term_position,value,value_slug
FROM catalogue_attributes
WHERE product_id=:product_ref
  AND (
    lower(COALESCE(attribute_name,'')) LIKE '%finish%'
    OR lower(COALESCE(attribute_name,'')) LIKE '%color%'
  )
ORDER BY attribute_position,term_position;
```

The parent dossier's `variations_json` contains published variation IDs, but no
independent variation price or stock is established. Do not turn those IDs into
products. Do not infer sibling product families from title similarity.

## 11. Compare an explicit shortlist

Resolve every identifier first, then retrieve complete dossiers for the exact
`product_ref` set. Comparison claims come from dossier fields and occurrence
arrays.

```sql
WITH requested(identifier) AS (
  VALUES(lower(trim(:first))), (lower(trim(:second))), (lower(trim(:third)))
), refs AS (
  SELECT DISTINCT i.product_ref
  FROM requested q JOIN product_identifiers i
    ON i.normalized_identifier=q.identifier
)
SELECT d.*
FROM refs r JOIN product_dossiers d USING(product_ref)
ORDER BY d.title,d.product_ref;
```

If an identifier is unresolved, say so rather than substituting a similar title.

## 12. Prefer stronger evidence without weakening eligibility

Rank products with more explicit occurrences after enforcing every requested
requirement.

```sql
SELECT r.product_ref,
       -(
         SELECT count(*) FROM resolved_spec_values s
         WHERE s.product_id=r.product_ref
           AND s.canonical_field IN (
             'voltage','cct','wattage','lumens','cri',
             'location_rating','safety_rating','dimming_protocols'
           )
       ) AS rank_score
FROM resolved_products r
WHERE lower(r.categories) LIKE '%outdoor%'
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref AND s.canonical_field='finish'
      AND lower(s.raw_value) LIKE '%black%'
  );
```

Evidence completeness can rank eligible products; it cannot make an ineligible
product eligible.

## 13. Combine complementary searches when one is too narrow

Search title/taxonomy terms and specification terms separately, then union
product identities before applying mandatory filters.

```sql
WITH title_hits AS (
  SELECT product_ref,bm25(products_fts) score
  FROM products_fts WHERE products_fts MATCH :title_terms
), spec_hits AS (
  SELECT product_ref,bm25(products_fts) score
  FROM products_fts WHERE products_fts MATCH :spec_terms
), discovered AS (
  SELECT product_ref,min(score) score
  FROM (
    SELECT * FROM title_hits
    UNION ALL
    SELECT * FROM spec_hits
  )
  GROUP BY product_ref
)
SELECT product_ref,score AS rank_score FROM discovered;
```

Unioning recall does not relax the later eligibility predicate.

## 14. Return distinct products, not repeated evidence rows

`spec_occurrences`, attributes, taxonomy, and images are one-to-many. Collapse
to distinct `product_ref` before dossier expansion.

```sql
WITH matched AS (
  SELECT DISTINCT r.product_ref
  FROM resolved_products r
  JOIN resolved_spec_values s ON s.product_id=r.product_ref
  WHERE lower(r.categories) LIKE '%chandelier%'
    AND s.canonical_field='finish'
    AND lower(s.raw_value) LIKE '%black%'
)
SELECT d.*
FROM matched m JOIN product_dossiers d USING(product_ref)
ORDER BY d.title,d.product_ref
LIMIT 5;
```

Do not deduplicate different native products merely because their titles look
similar.

## 15. Continue with transformed constraints

> “Show me more like these, but under $150.”

Carry forward the exact prior eligible predicate, add the new price limit, and
exclude the previously returned product identities. This example continues
recipe 5; it deliberately repeats every earlier mandatory constraint.

```sql
SELECT DISTINCT r.product_ref
FROM resolved_products r
WHERE (lower(r.title) LIKE '%high bay%' OR lower(r.categories) LIKE '%high bay%')
  AND r.cct_min<=5000 AND r.cct_max>=5000
  AND r.voltage_min<=277 AND r.voltage_max>=277
  AND r.lumens_max>=30000
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref
      AND s.canonical_field='dimming_protocols'
      AND replace(lower(s.raw_value),' ','') LIKE '%0-10v%'
  )
  AND EXISTS (
    SELECT 1 FROM resolved_spec_values s
    WHERE s.product_id=r.product_ref
      AND s.canonical_field='safety_rating'
      AND lower(s.raw_value) LIKE '%dlc%'
  )
  AND r.price_visibility='public' AND r.price_min<=150
  AND r.product_ref NOT IN (:shown_ref_1,:shown_ref_2,:shown_ref_3);
```

A continuation is a new query over canonical evidence, not a request to invent
unseen products. Preserve the original mandatory requirements unless the user
explicitly changes them.
