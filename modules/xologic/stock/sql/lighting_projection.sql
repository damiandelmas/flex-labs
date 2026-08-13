-- XOLogic lighting projection.
--
-- This script is intentionally a post-native projection.  It assumes that
-- products, taxonomy_paths, and spec_occurrences have already been populated.
-- The native occurrence relation remains the source of truth; the only
-- materialized derived relation is the indexed long-form parse below.

DROP VIEW IF EXISTS resolved_products;
DROP TABLE IF EXISTS resolved_spec_values;
DROP TABLE IF EXISTS spec_field_map;

CREATE TABLE spec_field_map (
  normalized_label TEXT PRIMARY KEY,
  canonical_field TEXT NOT NULL,
  value_kind TEXT NOT NULL CHECK (value_kind IN ('text','numeric','boolean','text_list')),
  precedence INTEGER NOT NULL,
  source_path TEXT NOT NULL
);

-- normalized_label is lower(trim(label)) after source whitespace has been
-- converted to ordinary spaces.  These are exact labels, not fuzzy matches.
-- The paths are stable documentation pointers in the lighting-specs-wiki.
INSERT INTO spec_field_map
  (normalized_label, canonical_field, value_kind, precedence, source_path)
VALUES
  ('product type',          'product_type',          'text',      10, 'lighting-specs-wiki/fields/product_type'),
  ('finish',                'finish',                'text',      10, 'lighting-specs-wiki/fields/finish'),
  ('style',                 'style',                 'text',      10, 'lighting-specs-wiki/fields/style'),
  ('material',              'material',              'text',      10, 'lighting-specs-wiki/fields/material'),
  ('location rating',       'location_rating',       'text',      10, 'lighting-specs-wiki/fields/location_rating'),
  ('safety rating',         'safety_rating',         'text',      10, 'lighting-specs-wiki/fields/safety_rating'),
  ('voltage',               'voltage',               'numeric',   10, 'lighting-specs-wiki/fields/voltage'),
  ('input voltage',         'voltage',               'numeric',   10, 'lighting-specs-wiki/fields/voltage'),
  ('input voltage range',   'voltage',               'numeric',   10, 'lighting-specs-wiki/fields/voltage'),
  ('led input voltage',     'voltage',               'numeric',   10, 'lighting-specs-wiki/fields/voltage'),
  ('operating voltage',     'voltage',               'numeric',   20, 'lighting-specs-wiki/fields/voltage'),
  ('cct',                   'cct',                   'numeric',   10, 'lighting-specs-wiki/fields/cct'),
  ('kelvin',                'cct',                   'numeric',   10, 'lighting-specs-wiki/fields/cct'),
  ('kelvin color',          'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('temperature (cct)',     'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('led cct light color',   'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('color temp',            'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('colour temp',           'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('color temprature',      'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('color temperature',     'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('colour temperature',    'cct',                   'numeric',   20, 'lighting-specs-wiki/fields/cct'),
  ('wattage',               'wattage',               'numeric',   10, 'lighting-specs-wiki/fields/wattage'),
  ('lumens',                'lumens',                'numeric',   10, 'lighting-specs-wiki/fields/lumens'),
  ('lumens / light output','lumens',                'numeric',   10, 'lighting-specs-wiki/fields/lumens'),
  ('light output',          'lumens',                'numeric',   10, 'lighting-specs-wiki/fields/lumens'),
  ('delivered lumens',      'delivered_lumens',      'numeric',   10, 'lighting-specs-wiki/fields/lumens'),
  ('light source lumens',   'light_source_lumens',   'numeric',   10, 'lighting-specs-wiki/fields/light_source_lumens'),
  ('light source lumen',    'light_source_lumens',   'numeric',   20, 'lighting-specs-wiki/fields/light_source_lumens'),
  ('max wattage',           'max_wattage',           'numeric',   10, 'lighting-specs-wiki/fields/wattage'),
  ('max watt',              'max_wattage',           'numeric',   20, 'lighting-specs-wiki/fields/wattage'),
  ('bulb wattage',          'bulb_wattage',          'numeric',   10, 'lighting-specs-wiki/fields/wattage'),
  ('cri',                   'cri',                   'numeric',   10, 'lighting-specs-wiki/fields/cri'),
  ('bulb cri',              'bulb_cri',              'numeric',   10, 'lighting-specs-wiki/fields/cri'),
  ('color rendering index', 'cri',                   'numeric',   20, 'lighting-specs-wiki/fields/cri'),
  ('colour rendering index','cri',                   'numeric',   20, 'lighting-specs-wiki/fields/cri'),
  ('ip rating',             'ip_rating',             'numeric',   10, 'lighting-specs-wiki/fields/ip_rating'),
  ('ingress protection',    'ip_rating',             'numeric',   20, 'lighting-specs-wiki/fields/ip_rating'),
  ('dimmable',              'fixture_dimmable',      'boolean',  10, 'lighting-specs-wiki/fields/fixture_dimmable'),
  ('fixture dimmable',      'fixture_dimmable',      'boolean',  20, 'lighting-specs-wiki/fields/fixture_dimmable'),
  ('bulb dimmable',         'bulb_dimmable',         'boolean',  10, 'lighting-specs-wiki/fields/bulb_dimmable'),
  ('dimming',               'dimming_protocols',     'text_list',10, 'lighting-specs-wiki/fields/dimming_protocols'),
  ('dimming type',          'dimming_protocols',     'text_list',10, 'lighting-specs-wiki/fields/dimming_protocols'),
  ('dimming method',        'dimming_protocols',     'text_list',10, 'lighting-specs-wiki/fields/dimming_protocols'),
  ('dimming protocol',      'dimming_protocols',     'text_list',10, 'lighting-specs-wiki/fields/dimming_protocols'),
  ('dimming protocols',     'dimming_protocols',     'text_list',20, 'lighting-specs-wiki/fields/dimming_protocols'),
  ('minimum dim percent',   'minimum_dim_percent',   'numeric',  10, 'lighting-specs-wiki/fields/minimum_dim_percent'),
  ('minimum dimming percent','minimum_dim_percent',  'numeric',  20, 'lighting-specs-wiki/fields/minimum_dim_percent');

CREATE INDEX ix_spec_field_map_field
  ON spec_field_map(canonical_field, precedence, normalized_label);

CREATE TABLE resolved_spec_values AS
WITH RECURSIVE
mapped AS (
  SELECT
    o.occurrence_id,
    o.product_ref,
    m.normalized_label,
    m.canonical_field,
    m.value_kind,
    m.precedence,
    m.source_path,
    o.label AS raw_label,
    o.value AS raw_value,
    o.raw_label AS native_raw_label,
    o.raw_value AS native_raw_value,
    o.source_layer,
    o.source_order,
    o.surface_ordinal,
    o.locator,
    o.visible,
    trim(replace(replace(replace(COALESCE(o.value,''), char(9), ' '), char(10), ' '), char(13), ' ')) AS value_trim,
    lower(trim(replace(replace(replace(COALESCE(o.value,''), char(9), ' '), char(10), ' '), char(13), ' '))) AS value_lower
  FROM spec_occurrences AS o
  JOIN spec_field_map AS m
    ON m.normalized_label = lower(trim(replace(replace(replace(COALESCE(o.label,''), char(9), ' '), char(10), ' '), char(13), ' ')))
  WHERE o.product_ref IS NOT NULL
),
cct_inputs AS (
  SELECT
    occurrence_id,
    replace(replace(replace(replace(replace(replace(replace(replace(lower(value_trim), 'kelvin', 'k'), char(160), ''), ' ', ''), ',', '/'), '&', '/'), '–', '/'), '—', '/'), '-', '/') || '/' AS rest
  FROM mapped
  WHERE canonical_field = 'cct'
),
cct_parts(occurrence_id, rest, part, part_no) AS (
  SELECT occurrence_id,
         substr(rest, instr(rest, '/') + 1),
         substr(rest, 1, instr(rest, '/') - 1),
         1
  FROM cct_inputs
  WHERE rest <> '/'
  UNION ALL
  SELECT occurrence_id,
         substr(rest, instr(rest, '/') + 1),
         substr(rest, 1, instr(rest, '/') - 1),
         part_no + 1
  FROM cct_parts
  WHERE rest <> ''
),
cct_tokens AS (
  SELECT
    occurrence_id,
    part_no,
    replace(replace(replace(part, 'k', ''), '°', ''), 'deg', '') AS token
  FROM cct_parts
  WHERE part <> ''
),
cct_bounds AS (
  SELECT
    occurrence_id,
    part_no,
    CASE WHEN instr(token, '-') > 0 THEN substr(token, 1, instr(token, '-') - 1) ELSE token END AS lo_text,
    CASE WHEN instr(token, '-') > 0 THEN substr(token, instr(token, '-') + 1) ELSE token END AS hi_text,
    token
  FROM cct_tokens
),
cct_numbers AS (
  SELECT
    occurrence_id,
    part_no,
    CASE WHEN lo_text IN ('27','30','35','40','50') THEN CAST(lo_text || '00' AS INTEGER) ELSE CAST(lo_text AS INTEGER) END AS numeric_min,
    CASE WHEN hi_text IN ('27','30','35','40','50') THEN CAST(hi_text || '00' AS INTEGER) ELSE CAST(hi_text AS INTEGER) END AS numeric_max,
    CASE
      WHEN lo_text <> '' AND hi_text <> ''
       AND lo_text GLOB '[0-9]*' AND hi_text GLOB '[0-9]*'
       AND lo_text NOT GLOB '*[^0-9]*' AND hi_text NOT GLOB '*[^0-9]*'
       AND instr(token, '-') <= length(token) - 1
       AND token NOT GLOB '*-*-' THEN 1
      ELSE 0
    END AS token_valid
  FROM cct_bounds
),
cct_aggregate AS (
  SELECT
    occurrence_id,
    CASE
      WHEN count(*) > 0
       AND sum(CASE WHEN token_valid = 1 AND numeric_min BETWEEN 1000 AND 10000
                              AND numeric_max BETWEEN 1000 AND 10000
                              AND numeric_min <= numeric_max THEN 1 ELSE 0 END) = count(*)
      THEN min(numeric_min)
    END AS numeric_min,
    CASE
      WHEN count(*) > 0
       AND sum(CASE WHEN token_valid = 1 AND numeric_min BETWEEN 1000 AND 10000
                              AND numeric_max BETWEEN 1000 AND 10000
                              AND numeric_min <= numeric_max THEN 1 ELSE 0 END) = count(*)
      THEN max(numeric_max)
    END AS numeric_max,
    CASE
      WHEN count(*) > 1 THEN 1 ELSE 0
    END AS is_selectable
  FROM cct_numbers
  GROUP BY occurrence_id
),
numeric_inputs AS (
  SELECT
    m.*,
    replace(replace(replace(replace(replace(lower(value_trim), '–', '-'), '—', '-'), char(160), ''), ' ', ''), '°', '') AS compact
  FROM mapped AS m
  WHERE canonical_field IN ('voltage','wattage','lumens','delivered_lumens','light_source_lumens','max_wattage','bulb_wattage','cri','bulb_cri','ip_rating','minimum_dim_percent')
),
numeric_forms AS (
  SELECT
    n.*,
    CASE
      WHEN canonical_field = 'voltage' AND instr(value_trim, ',') > 0 THEN NULL
      WHEN canonical_field = 'voltage' AND instr(compact, '/') > 0 THEN
        CASE
          WHEN instr(compact, 'v/') > 0
           AND substr(compact, instr(compact, 'v/') + 2) LIKE '%hz'
           AND substr(compact, instr(compact, 'v/') + 2) NOT LIKE '%/%'
           AND substr(substr(compact, instr(compact, 'v/') + 2), 1,
                      length(substr(compact, instr(compact, 'v/') + 2)) - 2) <> ''
           AND substr(substr(compact, instr(compact, 'v/') + 2), 1,
                      length(substr(compact, instr(compact, 'v/') + 2)) - 2) NOT GLOB '*[^0-9.]*'
          THEN substr(compact, 1, instr(compact, 'v/') - 1)
        END
      WHEN canonical_field = 'voltage' AND compact LIKE '%volts' THEN replace(compact, 'volts', '')
      WHEN canonical_field = 'voltage' AND compact LIKE '%vac' THEN replace(compact, 'vac', '')
      WHEN canonical_field = 'voltage' AND compact LIKE '%vdc' THEN replace(compact, 'vdc', '')
      WHEN canonical_field = 'voltage' AND compact LIKE '%v' THEN replace(compact, 'v', '')
      WHEN canonical_field = 'voltage' AND compact GLOB '*[^0-9.-]*' THEN NULL
      WHEN canonical_field = 'voltage' THEN compact
      WHEN canonical_field IN ('wattage','max_wattage','bulb_wattage') AND compact LIKE '%watts' THEN replace(replace(compact, 'watts', ''), '/', '-')
      WHEN canonical_field IN ('wattage','max_wattage','bulb_wattage') AND compact LIKE '%watt' THEN replace(replace(compact, 'watt', ''), '/', '-')
      WHEN canonical_field IN ('wattage','max_wattage','bulb_wattage') AND compact LIKE '%w' THEN replace(replace(compact, 'w', ''), '/', '-')
      WHEN canonical_field IN ('lumens','delivered_lumens','light_source_lumens') AND compact LIKE '%lumens' THEN replace(replace(compact, 'lumens', ''), ',', '')
      WHEN canonical_field IN ('lumens','delivered_lumens','light_source_lumens') AND compact LIKE '%lumen' THEN replace(replace(compact, 'lumen', ''), ',', '')
      WHEN canonical_field IN ('lumens','delivered_lumens','light_source_lumens') AND compact LIKE '%lm' THEN replace(replace(compact, 'lm', ''), ',', '')
      WHEN canonical_field IN ('cri','bulb_cri') AND (compact LIKE '%tm-30%' OR compact LIKE '%rf%') THEN NULL
      WHEN canonical_field IN ('cri','bulb_cri') AND compact LIKE '>%' THEN substr(compact, 2)
      WHEN canonical_field IN ('cri','bulb_cri') AND compact LIKE '%+' THEN substr(compact, 1, length(compact) - 1)
      WHEN canonical_field IN ('cri','bulb_cri') THEN compact
      WHEN canonical_field = 'ip_rating' AND compact GLOB 'ip[0-9][0-9]' THEN substr(compact, 3)
      WHEN canonical_field = 'ip_rating' THEN NULL
      WHEN canonical_field = 'minimum_dim_percent' AND compact LIKE '%\%%' ESCAPE '\' THEN substr(compact, 1, length(compact) - 1)
      ELSE compact
    END AS numeric_token,
    CASE WHEN canonical_field IN ('cri','bulb_cri') AND (compact LIKE '%+' OR compact LIKE '>%' ) THEN 1 ELSE 0 END AS is_lower_bound
  FROM numeric_inputs AS n
),
numeric_bounds AS (
  SELECT
    n.*,
    CASE WHEN instr(numeric_token, '-') > 0 THEN substr(numeric_token, 1, instr(numeric_token, '-') - 1) ELSE numeric_token END AS lo_text,
    CASE WHEN instr(numeric_token, '-') > 0 THEN substr(numeric_token, instr(numeric_token, '-') + 1) ELSE numeric_token END AS hi_text
  FROM numeric_forms AS n
),
numeric_parsed AS (
  SELECT
    occurrence_id,
    canonical_field,
    CASE
      WHEN numeric_token IS NOT NULL
       AND lo_text <> '' AND hi_text <> ''
       AND lo_text GLOB '[0-9]*' AND hi_text GLOB '[0-9]*'
       AND lo_text NOT GLOB '*[^0-9.]*' AND hi_text NOT GLOB '*[^0-9.]*'
       AND lo_text NOT GLOB '*.*.*' AND hi_text NOT GLOB '*.*.*'
       AND instr(numeric_token, '-') <= length(numeric_token) - 1
       AND numeric_token NOT GLOB '*-*-' THEN CAST(lo_text AS REAL)
    END AS numeric_min,
    CASE
      WHEN numeric_token IS NOT NULL
       AND lo_text <> '' AND hi_text <> ''
       AND lo_text GLOB '[0-9]*' AND hi_text GLOB '[0-9]*'
       AND lo_text NOT GLOB '*[^0-9.]*' AND hi_text NOT GLOB '*[^0-9.]*'
       AND lo_text NOT GLOB '*.*.*' AND hi_text NOT GLOB '*.*.*'
       AND instr(numeric_token, '-') <= length(numeric_token) - 1
       AND numeric_token NOT GLOB '*-*-' THEN
         CASE WHEN is_lower_bound = 1 THEN NULL ELSE CAST(hi_text AS REAL) END
    END AS numeric_max,
    is_lower_bound,
    CASE
      WHEN canonical_field <> 'voltage'
       AND (instr(numeric_token, '/') > 0 OR instr(numeric_token, ',') > 0 OR instr(numeric_token, '-') > 0)
      THEN 1 ELSE 0
    END AS is_selectable
  FROM numeric_bounds
),
parsed AS (
  SELECT
    m.*,
    CASE WHEN m.value_kind IN ('text','text_list') AND m.value_trim <> '' THEN m.value_trim END AS text_value,
    CASE WHEN m.canonical_field = 'cct' THEN c.numeric_min ELSE n.numeric_min END AS numeric_min,
    CASE WHEN m.canonical_field = 'cct' THEN c.numeric_max ELSE n.numeric_max END AS numeric_max,
    CASE WHEN m.canonical_field = 'cct' THEN c.is_selectable ELSE n.is_selectable END AS is_selectable,
    CASE
      WHEN m.canonical_field IN ('fixture_dimmable','bulb_dimmable') AND m.value_lower IN ('yes','y','true','1','dimmable','yes-dimmable') THEN 1
      WHEN m.canonical_field IN ('fixture_dimmable','bulb_dimmable') AND m.value_lower IN ('no','n','false','0','not dimmable','non-dimmable','non-dimmable') THEN 0
    END AS boolean_value,
    CASE
      WHEN m.canonical_field IN ('fixture_dimmable','bulb_dimmable') AND m.value_lower = 'lamp dependent' THEN 'absent_by_design'
      WHEN m.canonical_field IN ('fixture_dimmable','bulb_dimmable') AND m.value_lower IN ('yes','y','true','1','dimmable','yes-dimmable','no','n','false','0','not dimmable','non-dimmable') THEN 'parsed'
      WHEN m.canonical_field IN ('fixture_dimmable','bulb_dimmable') AND m.value_lower = 'lamp dependent' THEN 'absent_by_design'
      WHEN m.value_kind IN ('text','text_list') AND m.value_trim <> '' THEN 'parsed'
      WHEN m.canonical_field = 'cct' AND c.numeric_min IS NOT NULL THEN 'parsed'
      WHEN m.canonical_field IN ('voltage','wattage','lumens','delivered_lumens','light_source_lumens','max_wattage','bulb_wattage','cri','bulb_cri','ip_rating','minimum_dim_percent')
       AND n.numeric_min IS NOT NULL AND COALESCE(n.is_lower_bound, 0) = 1 THEN 'lower_bound'
      WHEN m.canonical_field IN ('voltage','wattage','lumens','delivered_lumens','light_source_lumens','max_wattage','bulb_wattage','cri','bulb_cri','ip_rating','minimum_dim_percent')
       AND n.numeric_min IS NOT NULL THEN 'parsed'
      ELSE 'unparsed'
    END AS parse_status,
    CASE
      WHEN m.canonical_field = 'voltage' THEN 'V'
      WHEN m.canonical_field = 'cct' THEN 'K'
      WHEN m.canonical_field IN ('wattage','max_wattage','bulb_wattage') THEN 'W'
      WHEN m.canonical_field IN ('lumens','delivered_lumens','light_source_lumens') THEN 'lm'
      WHEN m.canonical_field IN ('cri','bulb_cri') THEN 'CRI'
      WHEN m.canonical_field = 'ip_rating' THEN 'IP'
      WHEN m.canonical_field = 'minimum_dim_percent' THEN '%'
    END AS unit
  FROM mapped AS m
  LEFT JOIN cct_aggregate AS c ON c.occurrence_id = m.occurrence_id
  LEFT JOIN numeric_parsed AS n ON n.occurrence_id = m.occurrence_id
),
conflicts AS (
  SELECT product_ref, canonical_field
  FROM mapped
  GROUP BY product_ref, canonical_field
  HAVING count(DISTINCT lower(trim(raw_value))) > 1
),
field_label_counts AS (
  SELECT product_ref, canonical_field, count(DISTINCT normalized_label) AS label_count
  FROM parsed
  GROUP BY product_ref, canonical_field
),
ranked AS (
  SELECT
    p.*,
    f.label_count,
    CASE
      WHEN lower(replace(replace(COALESCE(p.source_layer,''), ' ', ''), '_', '')) IN ('additionaldata','additional') THEN 0
      WHEN lower(replace(replace(COALESCE(p.source_layer,''), ' ', ''), '_', '')) IN ('standarddata','standard') THEN 1
      ELSE 2
    END AS source_layer_rank,
    row_number() OVER (
      PARTITION BY p.product_ref, p.canonical_field
      ORDER BY
        CASE
          WHEN f.label_count > 1 THEN p.precedence
          ELSE COALESCE(p.visible, 0) * -1
        END,
        CASE
          WHEN f.label_count > 1 THEN
            COALESCE(p.visible, 0) * -1
          ELSE
            CASE
              WHEN lower(replace(replace(COALESCE(p.source_layer,''), ' ', ''), '_', '')) IN ('additionaldata','additional') THEN 0
              WHEN lower(replace(replace(COALESCE(p.source_layer,''), ' ', ''), '_', '')) IN ('standarddata','standard') THEN 1
              ELSE 2
            END
        END,
        CASE WHEN f.label_count > 1 THEN
          CASE
            WHEN lower(replace(replace(COALESCE(p.source_layer,''), ' ', ''), '_', '')) IN ('additionaldata','additional') THEN 0
            WHEN lower(replace(replace(COALESCE(p.source_layer,''), ' ', ''), '_', '')) IN ('standarddata','standard') THEN 1
            ELSE 2
          END
          ELSE p.precedence
        END,
        COALESCE(p.visible, 0) DESC,
        p.source_order,
        COALESCE(p.surface_ordinal, 0),
        p.occurrence_id
    ) AS preference_rank
  FROM parsed AS p
  JOIN field_label_counts AS f
    ON f.product_ref = p.product_ref
   AND f.canonical_field = p.canonical_field
)
SELECT
  r.occurrence_id,
  r.product_ref,
  r.canonical_field,
  r.normalized_label,
  r.value_kind,
  r.precedence,
  r.source_path,
  r.raw_label,
  r.raw_value,
  r.native_raw_label,
  r.native_raw_value,
  r.text_value,
  r.numeric_min,
  r.numeric_max,
  r.is_selectable,
  r.boolean_value,
  r.unit,
  r.parse_status,
  r.source_layer,
  r.source_layer_rank,
  r.source_order,
  r.surface_ordinal,
  r.locator,
  CASE WHEN r.preference_rank = 1 THEN 1 ELSE 0 END AS is_preferred,
  r.preference_rank,
  CASE WHEN c.product_ref IS NULL THEN 0 ELSE 1 END AS has_conflict,
  CASE WHEN c.product_ref IS NULL THEN 'consistent' ELSE 'conflict' END AS conflict_state
FROM ranked AS r
LEFT JOIN conflicts AS c
  ON c.product_ref = r.product_ref
 AND c.canonical_field = r.canonical_field
ORDER BY r.product_ref, r.canonical_field, r.preference_rank, r.occurrence_id;

CREATE INDEX ix_resolved_spec_values_product
  ON resolved_spec_values(product_ref, canonical_field, is_preferred, source_order, occurrence_id);
CREATE INDEX ix_resolved_spec_values_field
  ON resolved_spec_values(canonical_field, is_preferred, parse_status, numeric_min, numeric_max);
CREATE INDEX ix_resolved_spec_values_occurrence
  ON resolved_spec_values(occurrence_id);

CREATE VIEW resolved_products AS
WITH
variant_prices AS (
  SELECT
    p.product_ref,
    CASE
      WHEN trim(replace(replace(CAST(json_extract(v.value, '$.price') AS TEXT), '$', ''), ',', '')) <> ''
       AND trim(replace(replace(CAST(json_extract(v.value, '$.price') AS TEXT), '$', ''), ',', '')) GLOB '[0-9]*'
       AND trim(replace(replace(CAST(json_extract(v.value, '$.price') AS TEXT), '$', ''), ',', '')) NOT GLOB '*[^0-9.]*'
       AND trim(replace(replace(CAST(json_extract(v.value, '$.price') AS TEXT), '$', ''), ',', '')) NOT GLOB '*.*.*'
      THEN CAST(trim(replace(replace(CAST(json_extract(v.value, '$.price') AS TEXT), '$', ''), ',', '')) AS REAL)
    END AS variant_price
  FROM products AS p
  JOIN json_each(
    CASE WHEN json_valid(p.product_json) THEN p.product_json ELSE '{}' END,
    '$.variants'
  ) AS v
),
price_rollup AS (
  SELECT product_ref, min(variant_price) AS price_min, max(variant_price) AS price_max
  FROM variant_prices
  GROUP BY product_ref
),
taxonomy_rollup AS (
  SELECT
    product_ref,
    min(CASE WHEN depth = 1 THEN label END) AS category,
    min(CASE WHEN depth = 2 THEN label END) AS subcategory
  FROM taxonomy_paths
  GROUP BY product_ref
),
spec_rollup AS (
  SELECT
    product_ref,
    max(CASE WHEN canonical_field = 'product_type' THEN text_value END) AS product_type,
    max(CASE WHEN canonical_field = 'finish' THEN text_value END) AS finish,
    max(CASE WHEN canonical_field = 'style' THEN text_value END) AS style,
    max(CASE WHEN canonical_field = 'material' THEN text_value END) AS material,
    max(CASE WHEN canonical_field = 'location_rating' THEN text_value END) AS location_rating,
    max(CASE WHEN canonical_field = 'safety_rating' THEN text_value END) AS safety_rating,
    min(CASE WHEN canonical_field = 'voltage' THEN numeric_min END) AS voltage_min,
    max(CASE WHEN canonical_field = 'voltage' THEN numeric_max END) AS voltage_max,
    min(CASE WHEN canonical_field = 'cct' THEN numeric_min END) AS cct_min,
    max(CASE WHEN canonical_field = 'cct' THEN numeric_max END) AS cct_max,
    max(CASE WHEN canonical_field = 'cct' THEN is_selectable END) AS cct_selectable,
    min(CASE WHEN canonical_field = 'wattage' THEN numeric_min END) AS wattage_min,
    max(CASE WHEN canonical_field = 'wattage' THEN numeric_max END) AS wattage_max,
    max(CASE WHEN canonical_field = 'wattage' THEN is_selectable END) AS wattage_selectable,
    min(CASE WHEN canonical_field = 'max_wattage' THEN numeric_min END) AS max_wattage_min,
    max(CASE WHEN canonical_field = 'max_wattage' THEN numeric_max END) AS max_wattage_max,
    min(CASE WHEN canonical_field = 'bulb_wattage' THEN numeric_min END) AS bulb_wattage_min,
    max(CASE WHEN canonical_field = 'bulb_wattage' THEN numeric_max END) AS bulb_wattage_max,
    min(CASE WHEN canonical_field = 'lumens' THEN numeric_min END) AS lumens_min,
    max(CASE WHEN canonical_field = 'lumens' THEN numeric_max END) AS lumens_max,
    min(CASE WHEN canonical_field = 'delivered_lumens' THEN numeric_min END) AS delivered_lumens_min,
    max(CASE WHEN canonical_field = 'delivered_lumens' THEN numeric_max END) AS delivered_lumens_max,
    min(CASE WHEN canonical_field = 'light_source_lumens' THEN numeric_min END) AS light_source_lumens_min,
    max(CASE WHEN canonical_field = 'light_source_lumens' THEN numeric_max END) AS light_source_lumens_max,
    min(CASE WHEN canonical_field = 'cri' THEN numeric_min END) AS cri_min,
    max(CASE WHEN canonical_field = 'cri' THEN numeric_max END) AS cri_max,
    min(CASE WHEN canonical_field = 'bulb_cri' THEN numeric_min END) AS bulb_cri_min,
    max(CASE WHEN canonical_field = 'bulb_cri' THEN numeric_max END) AS bulb_cri_max,
    min(CASE WHEN canonical_field = 'ip_rating' THEN numeric_min END) AS ip_rating_min,
    max(CASE WHEN canonical_field = 'ip_rating' THEN numeric_max END) AS ip_rating_max,
    max(CASE WHEN canonical_field = 'fixture_dimmable' THEN boolean_value END) AS fixture_dimmable,
    max(CASE WHEN canonical_field = 'bulb_dimmable' THEN boolean_value END) AS bulb_dimmable,
    max(CASE WHEN canonical_field = 'dimming_protocols' THEN text_value END) AS dimming_protocols,
    min(CASE WHEN canonical_field = 'minimum_dim_percent' THEN numeric_min END) AS minimum_dim_percent_min,
    max(CASE WHEN canonical_field = 'minimum_dim_percent' THEN numeric_max END) AS minimum_dim_percent_max
  FROM resolved_spec_values
  WHERE is_preferred = 1
  GROUP BY product_ref
),
preferred_json_values AS (
  SELECT
    canonical_field,
    product_ref,
    CASE
      WHEN text_value IS NOT NULL THEN json_quote(text_value)
      WHEN boolean_value IS NOT NULL THEN CAST(boolean_value AS TEXT)
      WHEN numeric_min IS NOT NULL AND numeric_min = numeric_max THEN CAST(numeric_min AS TEXT)
      WHEN numeric_min IS NOT NULL THEN json_object('min', numeric_min, 'max', numeric_max)
      ELSE json_quote(raw_value)
    END AS value_json
  FROM resolved_spec_values
  WHERE is_preferred = 1
),
preferred_json AS (
  SELECT product_ref,
         json_group_object(canonical_field, json(value_json)) AS preferred_specs_json
  FROM (
    SELECT product_ref, canonical_field, value_json
    FROM preferred_json_values
    WHERE value_json IS NOT NULL
    ORDER BY product_ref, canonical_field
  )
  GROUP BY product_ref
)
SELECT
  p.*,
  tx.category,
  tx.subcategory,
  pr.price_min,
  pr.price_max,
  COALESCE(NULLIF(s.product_type, ''), tx.category) AS product_type,
  s.finish,
  s.style,
  s.material,
  s.location_rating,
  s.safety_rating,
  s.voltage_min,
  s.voltage_max,
  s.cct_min,
  s.cct_max,
  s.cct_selectable,
  s.wattage_min,
  s.wattage_max,
  s.wattage_selectable,
  s.max_wattage_min,
  s.max_wattage_max,
  s.bulb_wattage_min,
  s.bulb_wattage_max,
  s.lumens_min,
  s.lumens_max,
  CASE WHEN s.lumens_min = s.lumens_max THEN s.lumens_min ELSE s.lumens_min END AS lumens,
  s.delivered_lumens_min,
  s.delivered_lumens_max,
  CASE WHEN s.delivered_lumens_min = s.delivered_lumens_max
       THEN s.delivered_lumens_min ELSE s.delivered_lumens_min END AS delivered_lumens,
  s.light_source_lumens_min,
  s.light_source_lumens_max,
  CASE WHEN s.light_source_lumens_min = s.light_source_lumens_max
       THEN s.light_source_lumens_min ELSE s.light_source_lumens_min END AS light_source_lumens,
  s.cri_min,
  s.cri_max,
  CASE WHEN s.cri_min = s.cri_max THEN s.cri_min ELSE s.cri_min END AS cri,
  s.bulb_cri_min,
  s.bulb_cri_max,
  CASE WHEN s.bulb_cri_min = s.bulb_cri_max
       THEN s.bulb_cri_min ELSE s.bulb_cri_min END AS bulb_cri,
  s.ip_rating_min,
  s.ip_rating_max,
  CASE WHEN s.ip_rating_min = s.ip_rating_max THEN s.ip_rating_min ELSE s.ip_rating_min END AS ip_rating,
  s.fixture_dimmable,
  s.bulb_dimmable,
  s.dimming_protocols,
  s.minimum_dim_percent_min,
  s.minimum_dim_percent_max,
  CASE WHEN s.minimum_dim_percent_min = s.minimum_dim_percent_max
       THEN s.minimum_dim_percent_min ELSE s.minimum_dim_percent_min END AS minimum_dim_percent,
  COALESCE(j.preferred_specs_json, '{}') AS preferred_specs_json
FROM products AS p
LEFT JOIN price_rollup AS pr ON pr.product_ref = p.product_ref
LEFT JOIN taxonomy_rollup AS tx ON tx.product_ref = p.product_ref
LEFT JOIN spec_rollup AS s ON s.product_ref = p.product_ref
LEFT JOIN preferred_json AS j ON j.product_ref = p.product_ref;
