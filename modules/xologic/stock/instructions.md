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

SQLite FTS5 requires the actual virtual-table name as the left operand of
`MATCH` and as the argument to `bm25()`. Do not alias `products_fts`; write
`products_fts MATCH ...` and `bm25(products_fts,...)` exactly.

When the tool returns a retryable SQL error, preserve the customer's complete
constraint set, correct only the failed SQL, and call the tool once more. Do not
broaden the request merely to make a failed query return rows.

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

Every product batch should expose `total_matches`, `returned`, and `has_more`.
Compute `total_matches` from the complete ranked population before product and
character limits, compute `returned` from the final batch, and set `has_more`
when the former exceeds the latter. This distinguishes an exhausted result set
from an intentionally bounded batch and supports a faithful “show me more” turn.

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

The examples progress from ordinary catalogue work to the few stronger patterns
that are useful when a simple query is not enough. Prefer the earliest example
that faithfully expresses the request.

## 1. Resolve an identifier before searching descriptions

> “What finishes does the Kichler 43999 five-light chandelier come in?”

In order to resolve a SKU, part number, GTIN, or handle, normalize the isolated
identifier and query `product_identifiers` directly.

```sql
WITH wanted AS (
  SELECT lower(trim(:identifier)) AS identifier
), discovered AS (
  SELECT DISTINCT i.product_ref
  FROM product_identifiers i
  CROSS JOIN wanted w
  WHERE i.normalized_identifier=w.identifier
     OR i.normalized_identifier LIKE w.identifier||'%'
), ranked AS (
  SELECT x.product_ref,
         row_number() OVER (ORDER BY p.sku,p.product_ref) AS batch_rank
  FROM discovered x
  JOIN resolved_products p USING(product_ref)
), sized AS (
  SELECT x.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY x.batch_rank
         ) AS cumulative_chars
  FROM ranked x
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized x
JOIN product_dossiers dossier USING(product_ref)
WHERE x.batch_rank<=5
  AND (x.batch_rank=1 OR x.cumulative_chars<=30000)
ORDER BY x.batch_rank;
```

The identifier relation establishes identity rather than textual similarity.
A base number may legitimately retain several published finish or size options.
Use descriptive retrieval only when the identifier route returns no product.

## 2. Run a simple descriptive product search

> “Show me pendant lights.”

In order to answer an exploratory category request, use FTS for recall and
confirm the requested merchandise class before returning complete products.

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
  WHERE lower(COALESCE(p.product_type,'')) LIKE '%pendant%'
     OR EXISTS (
       SELECT 1 FROM taxonomy_paths t
       WHERE t.product_ref=p.product_ref
         AND lower(t.label) LIKE '%pendant%'
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

FTS supplies a useful order across title, taxonomy, vendor, and specifications.
The category check prevents a loosely related search hit from becoming a result.
Add only the constraints the customer actually stated.

## 3. Apply ordinary must-have filters

> “Show me five black bathroom vanity lights under $200.”

In order to satisfy a normal product-finding request, discover broadly and put
every disqualifying requirement in the eligible predicate.

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
    AND lower(COALESCE(p.finish,'')) LIKE '%black%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%vanity%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%bathroom%'
      )
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

Price, finish, and bathroom-vanity classification are mandatory together. FTS
orders the qualifying population but cannot compensate for a missing property.
An empty result does not authorize a wider budget or different finish.

## 4. Allow alternatives while enforcing exclusions

> “Black or bronze wall sconces under $300, but no Nuvo and no integrated LED.”

In order to express permitted alternatives, group them inside one mandatory
condition and keep every exclusion explicit.

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
    AND (
      lower(COALESCE(p.finish,'')) LIKE '%black%'
      OR lower(COALESCE(p.finish,'')) LIKE '%bronze%'
    )
    AND lower(COALESCE(p.vendor,''))<>lower(:excluded_vendor)
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%sconce%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%sconce%'
      )
    )
    AND NOT EXISTS (
      SELECT 1 FROM spec_occurrences s
      WHERE s.product_ref=p.product_ref
        AND lower(s.value) LIKE '%integrated led%'
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

Black and bronze are one allowed finish group rather than two separate searches.
The vendor and light-source exclusions remain in force for every candidate.
`NOT EXISTS` prevents relevance from reintroducing a forbidden specification.

## 5. Require several published specifications together

> “Find a four-light traditional brass vanity with seeded glass.”

In order to establish a precise match, validate every requested property against
the resolved product and its published specification occurrences.

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
  WHERE lower(COALESCE(p.finish,'')) LIKE '%brass%'
    AND lower(COALESCE(p.style,'')) LIKE '%traditional%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%vanity%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%vanity%'
      )
    )
    AND EXISTS (
      SELECT 1 FROM spec_occurrences s
      WHERE s.product_ref=p.product_ref
        AND lower(s.label)='number of bulbs'
        AND lower(s.value) LIKE '4 bulb%'
    )
    AND EXISTS (
      SELECT 1 FROM spec_occurrences s
      WHERE s.product_ref=p.product_ref
        AND (
          lower(s.label) LIKE '%glass%'
          OR lower(s.label) LIKE '%shade%'
        )
        AND lower(s.value) LIKE '%seeded%'
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

Four lights, traditional style, brass finish, vanity class, and seeded glass are
all admission conditions. A dossier qualifies only when every condition has
published support. A high text score alone never establishes an exact match.

## 6. Apply trusted numeric limits

> “Find black outdoor wall lights under $300 with at least 1,000 lumens that support 120 volts.”

In order to use ranges safely, filter only on numeric fields that the resolved
contract has parsed and promoted.

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
    AND p.lumens_max>=:minimum_lumens
    AND p.voltage_min<=:required_voltage
    AND p.voltage_max>=:required_voltage
    AND lower(COALESCE(p.finish,'')) LIKE '%black%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%outdoor wall%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%outdoor wall%'
      )
    )
), ranked AS (
  SELECT e.*,
         row_number() OVER (
           ORDER BY e.price_min,e.text_rank,e.title,e.product_ref
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

The price, lumen, and voltage tests use resolved numeric values rather than raw
text comparisons. `NULL` fails a mandatory numeric condition because the value
is not published or safely resolved. The original strings remain available in
the complete dossier when notation matters.

## 7. Count and display the same eligible population

> “How many black bathroom vanity lights are under $200? Show me five.”

In order to make an inventory claim, compute the total before batching from the
same eligible predicate used for the displayed products.

```sql
WITH eligible AS (
  SELECT p.product_ref,p.price_min,p.title
  FROM resolved_products p
  WHERE p.price_min<=:max_price
    AND lower(COALESCE(p.finish,'')) LIKE '%black%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%vanity%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%bathroom%'
      )
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
           ORDER BY e.price_min,e.title,e.product_ref
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

The total describes all eligible products, not merely the returned page.
The dossier and character limits affect presentation only. Changing a filter
requires recomputing both the total and the displayed batch.

## 8. Treat a preference as a preference

> “Show me brass pendants under $600; modern would be ideal.”

In order to honor a non-mandatory preference, establish eligibility first and
use the preferred quality only to order the qualifying products.

```sql
WITH eligible AS (
  SELECT p.product_ref,p.price_min,p.title,p.style
  FROM resolved_products p
  WHERE p.price_min<=:max_price
    AND lower(COALESCE(p.finish,'')) LIKE '%brass%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%pendant%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%pendant%'
      )
    )
), ranked AS (
  SELECT e.*,
         row_number() OVER (
           ORDER BY
             CASE WHEN lower(COALESCE(e.style,'')) LIKE '%modern%'
                  THEN 0 ELSE 1 END,
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

Brass, pendant classification, and price determine admission. Modern style can
move a qualifying product upward but cannot admit an ineligible product. If the
customer makes modern mandatory, move it into the eligible predicate.

## 9. Label exact and nearby results separately

> “Find a four-light traditional brass vanity with seeded glass; transitional is acceptable only as an alternative.”

In order to offer nearby products without mislabeling them, preserve all hard
requirements and classify only the explicitly relaxed property.

```sql
WITH candidates AS (
  SELECT p.product_ref,p.price_min,p.title,p.style
  FROM resolved_products p
  WHERE lower(COALESCE(p.finish,'')) LIKE '%brass%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%vanity%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%vanity%'
      )
    )
    AND EXISTS (
      SELECT 1 FROM spec_occurrences s
      WHERE s.product_ref=p.product_ref
        AND lower(s.label)='number of bulbs'
        AND lower(s.value) LIKE '4 bulb%'
    )
    AND EXISTS (
      SELECT 1 FROM spec_occurrences s
      WHERE s.product_ref=p.product_ref
        AND (
          lower(s.label) LIKE '%glass%'
          OR lower(s.label) LIKE '%shade%'
        )
        AND lower(s.value) LIKE '%seeded%'
    )
), classified AS (
  SELECT c.*,
         CASE
           WHEN lower(COALESCE(c.style,'')) LIKE '%traditional%'
             THEN 'exact'
           WHEN :allow_transitional=1
            AND lower(COALESCE(c.style,'')) LIKE '%transitional%'
             THEN 'nearby: transitional instead of traditional'
         END AS match_class
  FROM candidates c
), ranked AS (
  SELECT c.*,
         row_number() OVER (
           ORDER BY CASE c.match_class WHEN 'exact' THEN 0 ELSE 1 END,
                    c.price_min,c.title,c.product_ref
         ) AS batch_rank
  FROM classified c
  WHERE c.match_class IS NOT NULL
), sized AS (
  SELECT r.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY r.batch_rank
         ) AS cumulative_chars
  FROM ranked r
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       r.match_class,dossier.*
FROM sized r
JOIN product_dossiers dossier USING(product_ref)
WHERE r.batch_rank<=5
  AND (r.batch_rank=1 OR r.cumulative_chars<=30000)
ORDER BY r.batch_rank;
```

Bulb count, finish, vanity class, and seeded glass remain mandatory throughout.
Only the style boundary changes, and only after the customer permits it. The
returned class states the precise difference the answer must disclose.

## 10. Follow published links to product options

> “What other finishes does this fixture come in?”

In order to resolve a product family, follow the selected dossier's published
variant URLs and map those URLs back to resolved product identities.

```sql
WITH selected AS (
  SELECT product_ref,url,variants_json
  FROM product_dossiers
  WHERE product_ref=:product_ref
), family_urls AS (
  SELECT url FROM selected
  UNION
  SELECT json_extract(v.value,'$[1]')
  FROM selected s
  JOIN json_each(s.variants_json) v ON true
), discovered AS (
  SELECT DISTINCT p.product_ref,p.finish,p.sku
  FROM family_urls f
  JOIN resolved_products p ON p.url=f.url
), ranked AS (
  SELECT x.*,
         row_number() OVER (
           ORDER BY x.finish,x.sku,x.product_ref
         ) AS batch_rank
  FROM discovered x
), sized AS (
  SELECT x.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY x.batch_rank
         ) AS cumulative_chars
  FROM ranked x
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized x
JOIN product_dossiers dossier USING(product_ref)
WHERE x.batch_rank<=5
  AND (x.batch_rank=1 OR x.cumulative_chars<=30000)
ORDER BY x.batch_rank;
```

Published links establish the family more reliably than similar titles. Every
resolved option supplies its own SKU, finish, price, and exact URL. Do not infer
an option that is absent from the linked catalogue records.

## 11. Compare an explicit shortlist

> “Compare these three fixtures.”

In order to compare products already selected in the conversation, pass their
exact `product_ref` values and retrieve the corresponding complete dossiers.

```sql
WITH requested AS (
  SELECT CAST(key AS integer) AS requested_order,value AS product_ref
  FROM json_each(:product_refs_json)
), ranked AS (
  SELECT r.product_ref,
         row_number() OVER (
           ORDER BY r.requested_order
         ) AS batch_rank
  FROM requested r
  JOIN product_dossiers d USING(product_ref)
), sized AS (
  SELECT r.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY r.batch_rank
         ) AS cumulative_chars
  FROM ranked r
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized r
JOIN product_dossiers dossier USING(product_ref)
WHERE r.batch_rank<=5
  AND (r.batch_rank=1 OR r.cumulative_chars<=30000)
ORDER BY r.batch_rank;
```

The input identities must come from earlier catalogue results rather than model
memory or reconstructed URLs. Complete dossiers place every published comparison
field and conflict in scope. Compare only like-for-like fields that the products
actually publish.

## 12. Prefer stronger evidence without weakening eligibility

> “Find black wet-rated outdoor wall lights, prioritizing products with complete electrical and output specifications.”

In order to prefer well-documented products, establish mandatory suitability
first and use evidence coverage and conflict state only for ordering.

```sql
WITH eligible AS (
  SELECT p.product_ref,p.price_min,p.title,
         (
           (p.voltage_min IS NOT NULL AND p.voltage_max IS NOT NULL)
           + (p.wattage_max IS NOT NULL)
           + (p.lumens_max IS NOT NULL)
           + (p.ip_rating_min IS NOT NULL)
           + (p.fixture_dimmable IS NOT NULL)
         ) AS evidence_coverage,
         (
           SELECT count(*) FROM spec_conflicts c
           WHERE c.product_ref=p.product_ref
             AND lower(c.label) IN ('finish','location rating','voltage')
         ) AS consequential_conflicts
  FROM resolved_products p
  WHERE lower(COALESCE(p.finish,'')) LIKE '%black%'
    AND (
      lower(COALESCE(p.location_rating,'')) LIKE '%wet%'
      OR p.ip_rating_min>=65
    )
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%outdoor wall%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%outdoor wall%'
      )
    )
), ranked AS (
  SELECT e.*,
         row_number() OVER (
           ORDER BY e.consequential_conflicts,
                    e.evidence_coverage DESC,
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       e.evidence_coverage,e.consequential_conflicts,dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

Black finish, location suitability, and outdoor-wall classification determine
eligibility. Coverage rewards published evidence but never invents a missing
fact. Any consequential conflict remains visible in the complete dossier.

## 13. Combine complementary searches when one is too narrow

> “Find brass vanities with seeded glass.”

In order to improve recall when wording is distributed across catalogue fields,
run a small number of focused FTS searches and merge their candidate identities.

```sql
WITH title_lane AS (
  SELECT d.product_ref,
         bm25(products_fts,0.0,8.0,12.0,4.0,5.0,2.0,1.0) AS text_rank
  FROM products_fts
  JOIN product_search_documents d ON d.rowid=products_fts.rowid
  WHERE products_fts MATCH :title_terms
), specification_lane AS (
  SELECT d.product_ref,
         bm25(products_fts,0.0,8.0,12.0,4.0,5.0,2.0,1.0) AS text_rank
  FROM products_fts
  JOIN product_search_documents d ON d.rowid=products_fts.rowid
  WHERE products_fts MATCH :specification_terms
), combined AS (
  SELECT product_ref,count(*) AS supporting_searches,min(text_rank) AS best_rank
  FROM (
    SELECT * FROM title_lane
    UNION ALL
    SELECT * FROM specification_lane
  )
  GROUP BY product_ref
), eligible AS (
  SELECT c.*,p.price_min,p.title
  FROM combined c
  JOIN resolved_products p USING(product_ref)
  WHERE lower(COALESCE(p.finish,'')) LIKE '%brass%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%vanity%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%vanity%'
      )
    )
    AND EXISTS (
      SELECT 1 FROM spec_occurrences s
      WHERE s.product_ref=p.product_ref
        AND (
          lower(s.label) LIKE '%glass%'
          OR lower(s.label) LIKE '%shade%'
        )
        AND lower(s.value) LIKE '%seeded%'
    )
), ranked AS (
  SELECT e.*,
         row_number() OVER (
           ORDER BY e.supporting_searches DESC,e.best_rank,
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       e.supporting_searches,dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

Each focused search contributes candidates from a different evidence surface.
Products supported by both searches rank first, but support count is not proof.
Finish, merchandise class, and seeded-glass evidence are still validated once.

## 14. Return distinct designs rather than repeated variants

> “Show me five distinct black chandelier designs under $500 from different brands when possible.”

In order to produce a useful assortment, collapse published variants into
families before distributing the remaining choices across vendors.

```sql
WITH family_map AS (
  SELECT d.product_ref,
         COALESCE(min(json_extract(v.value,'$[1]')),d.url) AS family_key
  FROM product_dossiers d
  LEFT JOIN json_each(d.variants_json) v ON true
  GROUP BY d.product_ref,d.url
), eligible AS (
  SELECT p.product_ref,p.vendor,p.price_min,p.title,f.family_key
  FROM resolved_products p
  JOIN family_map f USING(product_ref)
  WHERE p.price_min<=:max_price
    AND lower(COALESCE(p.finish,'')) LIKE '%black%'
    AND (
      lower(COALESCE(p.product_type,'')) LIKE '%chandelier%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%chandelier%'
      )
    )
), family_ranked AS (
  SELECT e.*,
         row_number() OVER (
           PARTITION BY e.family_key
           ORDER BY e.price_min,e.title,e.product_ref
         ) AS family_pick
  FROM eligible e
), representatives AS (
  SELECT * FROM family_ranked WHERE family_pick=1
), vendor_ranked AS (
  SELECT r.*,
         row_number() OVER (
           PARTITION BY r.vendor
           ORDER BY r.price_min,r.title,r.product_ref
         ) AS vendor_pick
  FROM representatives r
), ranked AS (
  SELECT v.*,
         row_number() OVER (
           ORDER BY v.vendor_pick,v.price_min,v.title,v.product_ref
         ) AS batch_rank
  FROM vendor_ranked v
), sized AS (
  SELECT r.*,
         sum(length(payload.dossier_json)) OVER (
           ORDER BY r.batch_rank
         ) AS cumulative_chars
  FROM ranked r
  JOIN product_dossier_payloads payload USING(product_ref)
)
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized r
JOIN product_dossiers dossier USING(product_ref)
WHERE r.batch_rank<=5
  AND (r.batch_rank=1 OR r.cumulative_chars<=30000)
ORDER BY r.batch_rank;
```

Published option links determine families instead of title resemblance. One
representative from each family prevents finish variants from filling the page.
Vendor round-robin broadens the shortlist without weakening any requirement.

## 15. Transform constraints for a follow-up

> “Show me more like these, but under $150 and not Nuvo.”

In order to continue a product search, preserve the prior mandatory constraints,
apply only the requested changes, and exclude every identity already shown.

```sql
WITH shown AS (
  SELECT value AS product_ref
  FROM json_each(:shown_product_refs_json)
), discovered AS (
  SELECT d.product_ref,
         bm25(products_fts,0.0,8.0,12.0,4.0,5.0,2.0,1.0) AS text_rank
  FROM products_fts
  JOIN product_search_documents d ON d.rowid=products_fts.rowid
  WHERE products_fts MATCH :original_terms
), eligible AS (
  SELECT x.product_ref,x.text_rank,p.price_min,p.title
  FROM discovered x
  JOIN resolved_products p USING(product_ref)
  WHERE p.price_min<=:new_price_ceiling
    AND lower(COALESCE(p.finish,''))
          LIKE '%'||lower(:required_finish)||'%'
    AND (
      lower(COALESCE(p.product_type,''))
        LIKE '%'||lower(:required_type)||'%'
      OR EXISTS (
        SELECT 1 FROM taxonomy_paths t
        WHERE t.product_ref=p.product_ref
          AND lower(t.label) LIKE '%'||lower(:required_type)||'%'
      )
    )
    AND (
      :required_style IS NULL
      OR lower(COALESCE(p.style,''))
           LIKE '%'||lower(:required_style)||'%'
    )
    AND lower(COALESCE(p.vendor,''))<>lower(:excluded_vendor)
    AND NOT EXISTS (
      SELECT 1 FROM shown s
      WHERE s.product_ref=p.product_ref
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
SELECT (SELECT count(*) FROM ranked) AS total_matches,
       count(*) OVER () AS returned,
       (SELECT count(*) FROM ranked)>count(*) OVER () AS has_more,
       dossier.*
FROM sized e
JOIN product_dossiers dossier USING(product_ref)
WHERE e.batch_rank<=5
  AND (e.batch_rank=1 OR e.cumulative_chars<=30000)
ORDER BY e.batch_rank;
```

The prior product class, finish, style, and exclusions remain active unless the
customer changes them. The new budget and vendor exclusion modify only those
parts of the constraint set. An empty continuation does not authorize repeated
products or silent relaxation.

Use only fields published by the resolved contract. Compute a count from the
same eligible predicate before making a total-inventory claim.
