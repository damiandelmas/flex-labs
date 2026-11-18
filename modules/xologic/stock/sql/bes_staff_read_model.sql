-- BES Staff Assistant catalogue read model.
--
-- Apply once to the existing source-faithful BES XOLogic catalogue. Native
-- capture tables remain intact. This hard-migrates the agent-facing surface to:
--   products, product_specifications, product_search_fts, product_details.

PRAGMA temp_store=FILE;
BEGIN IMMEDIATE;

DROP VIEW IF EXISTS product_details;
DROP TABLE IF EXISTS _product_detail_listings;
DROP TABLE IF EXISTS _product_detail_specifications;
DROP TABLE IF EXISTS _product_detail_conflicts;
DROP TABLE IF EXISTS _product_detail_options;
DROP TABLE IF EXISTS _product_detail_resources;
DROP TABLE IF EXISTS product_specifications;

-- Materialize the selected-current literal specification occurrences once.
-- label_norm/value_norm are mechanical search keys; label/value remain exact.
CREATE TABLE product_specifications AS
SELECT
  product_ref,
  fact_order AS specification_order,
  section,
  source_layer,
  label,
  value,
  lower(trim(COALESCE(label,''))) AS label_norm,
  lower(trim(COALESCE(value,''))) AS value_norm
FROM product_facts;

CREATE INDEX product_specifications_product_label
  ON product_specifications(product_ref,label_norm,specification_order);
CREATE INDEX product_specifications_label_product
  ON product_specifications(label_norm,product_ref);

-- Replace the identity-only view with the compact one-row product read model.
DROP VIEW products;
CREATE TABLE products AS
WITH
listing_values AS (
  SELECT
    l.product_ref,
    l.occurrence_id AS listing_id,
    l.source_order AS listing_order,
    l.title,
    l.image_url,
    l.price_text,
    l.call_for_price_text,
    l.show_price,
    l.show_cfp AS show_call_for_price,
    CASE
      WHEN trim(COALESCE(l.price_text,'')) GLOB '$[0-9]*'
      THEN CAST(replace(substr(trim(l.price_text),2),',','') AS REAL)
    END AS price_value
  FROM product_listing_occurrences AS l
),
listing_ranked AS (
  SELECT
    l.*,
    row_number() OVER (
      PARTITION BY l.product_ref
      ORDER BY
        CASE WHEN l.price_value IS NULL THEN 1 ELSE 0 END,
        l.price_value,
        l.listing_order,
        l.listing_id
    ) AS display_rank,
    min(l.price_value) OVER (PARTITION BY l.product_ref) AS price_min,
    max(l.price_value) OVER (PARTITION BY l.product_ref) AS price_max
  FROM listing_values AS l
),
ordered_spec_values AS (
  SELECT DISTINCT product_ref,label_norm,value_norm,value
  FROM product_specifications
  WHERE trim(COALESCE(value,''))<>''
    AND label_norm IN (
      'product type','category','subcategory','finish','style','material',
      'location rating','safety rating','ada compliant','ada complaint',
      'dimmable','fixture dimmable','voltage','input voltage','kelvin','cct',
      'wattage','lumens','number of lights','number of bulbs'
    )
  ORDER BY product_ref,label_norm,value_norm,value
),
spec_rollup AS (
  SELECT
    product_ref,
    group_concat(value,' | ') FILTER (WHERE label_norm='product type') AS published_product_type,
    group_concat(value,' | ') FILTER (WHERE label_norm='category') AS category,
    group_concat(value,' | ') FILTER (WHERE label_norm='subcategory') AS subcategory,
    group_concat(value,' | ') FILTER (WHERE label_norm='finish') AS finish,
    group_concat(value,' | ') FILTER (WHERE label_norm='style') AS style,
    group_concat(value,' | ') FILTER (WHERE label_norm='material') AS material,
    group_concat(value,' | ') FILTER (WHERE label_norm='location rating') AS location_rating,
    group_concat(value,' | ') FILTER (WHERE label_norm='safety rating') AS safety_rating,
    group_concat(value,' | ') FILTER (WHERE label_norm IN ('ada compliant','ada complaint')) AS ada_compliant,
    group_concat(value,' | ') FILTER (WHERE label_norm IN ('dimmable','fixture dimmable')) AS dimmable,
    group_concat(value,' | ') FILTER (WHERE label_norm IN ('voltage','input voltage')) AS voltage,
    group_concat(value,' | ') FILTER (WHERE label_norm IN ('kelvin','cct')) AS color_temperature,
    group_concat(value,' | ') FILTER (WHERE label_norm='wattage') AS wattage,
    group_concat(value,' | ') FILTER (WHERE label_norm='lumens') AS lumens,
    group_concat(value,' | ') FILTER (WHERE label_norm='number of lights') AS number_of_lights,
    group_concat(value,' | ') FILTER (WHERE label_norm='number of bulbs') AS number_of_bulbs
  FROM ordered_spec_values
  GROUP BY product_ref
)
SELECT
  p.product_ref,
  p.brand,
  p.url,
  CASE
    WHEN instr(lower(p.url),'/sku-')>0
    THEN substr(p.url,instr(lower(p.url),'/sku-')+5)
  END AS sku,
  l.title,
  l.image_url,
  l.price_text,
  l.price_value,
  l.price_min,
  l.price_max,
  l.call_for_price_text,
  l.show_price,
  l.show_call_for_price,
  s.category,
  s.subcategory,
  COALESCE(NULLIF(s.published_product_type,''),s.subcategory,s.category) AS product_type,
  s.finish,
  s.style,
  s.material,
  s.location_rating,
  s.safety_rating,
  s.ada_compliant,
  s.dimmable,
  s.voltage,
  s.color_temperature,
  s.wattage,
  s.lumens,
  s.number_of_lights,
  s.number_of_bulbs
FROM product_pages AS p
LEFT JOIN listing_ranked AS l
  ON l.product_ref=p.product_ref AND l.display_rank=1
LEFT JOIN spec_rollup AS s USING(product_ref);

CREATE UNIQUE INDEX products_product_ref ON products(product_ref);
CREATE INDEX products_price ON products(price_value,product_ref);
CREATE INDEX products_brand ON products(brand COLLATE NOCASE,product_ref);

-- Reuse the complete current FTS index; renaming FTS5 preserves its contents.
ALTER TABLE product_fts_search RENAME TO product_search_fts;

-- Materialize each complete-product component once. The underscore tables are
-- internal read-model components; product_details is the public hydration row.
CREATE TABLE _product_detail_listings AS
SELECT product_ref,
       json_group_array(json_array(
         listing_id,listing_order,title,image_url,price_text,
         call_for_price_text,show_price,show_call_for_price
       )) AS listings_json
FROM (
  SELECT * FROM product_listings
  ORDER BY product_ref,listing_order,listing_id
)
GROUP BY product_ref;
CREATE UNIQUE INDEX _product_detail_listings_product
  ON _product_detail_listings(product_ref);

CREATE TABLE _product_detail_specifications AS
SELECT product_ref,
       json_group_array(json_array(
         specification_order,section,source_layer,label,value
       )) AS specifications_json
FROM (
  SELECT product_ref,specification_order,section,source_layer,label,value
  FROM product_specifications
  ORDER BY product_ref,specification_order,section,source_layer,label,value
)
GROUP BY product_ref;
CREATE UNIQUE INDEX _product_detail_specifications_product
  ON _product_detail_specifications(product_ref);

CREATE TABLE _product_detail_conflicts AS
SELECT product_ref,
       json_group_array(json_array(label,distinct_values,json(values_seen))) AS conflicts_json
FROM (
  SELECT product_ref,
         min(label) AS label,
         count(DISTINCT value_norm) AS distinct_values,
         json_group_array(DISTINCT value) AS values_seen
  FROM product_specifications
  WHERE label_norm<>'' AND value_norm<>''
  GROUP BY product_ref,label_norm
  HAVING count(DISTINCT value_norm)>1
  ORDER BY product_ref,label_norm
)
GROUP BY product_ref;
CREATE UNIQUE INDEX _product_detail_conflicts_product
  ON _product_detail_conflicts(product_ref);

CREATE TABLE _product_detail_options AS
SELECT product_ref,
       json_group_array(json_array(
         option_order,section,option_label,option_title,value_text,selected,url
       )) AS options_json
FROM (
  SELECT * FROM product_options
  ORDER BY product_ref,section,option_order,url
)
GROUP BY product_ref;
CREATE UNIQUE INDEX _product_detail_options_product
  ON _product_detail_options(product_ref);

CREATE TABLE _product_detail_resources AS
SELECT product_ref,
       json_group_array(json_array(
         resource_order,resource_type,label,url
       )) AS resources_json
FROM (
  SELECT * FROM product_resources
  ORDER BY product_ref,resource_type,resource_order,url
)
GROUP BY product_ref;
CREATE UNIQUE INDEX _product_detail_resources_product
  ON _product_detail_resources(product_ref);

CREATE VIEW product_details AS
SELECT p.*,
       COALESCE(l.listings_json,'[]') AS listings_json,
       COALESCE(s.specifications_json,'[]') AS specifications_json,
       COALESCE(c.conflicts_json,'[]') AS conflicts_json,
       COALESCE(o.options_json,'[]') AS options_json,
       COALESCE(r.resources_json,'[]') AS resources_json,
       length(COALESCE(l.listings_json,'[]'))
       + length(COALESCE(s.specifications_json,'[]'))
       + length(COALESCE(c.conflicts_json,'[]'))
       + length(COALESCE(o.options_json,'[]'))
       + length(COALESCE(r.resources_json,'[]')) AS detail_payload_chars,
       json_object(
         'details_complete',json('true'),
         'specifications_complete',json('true'),
         'product_ref',p.product_ref,
         'sku',p.sku,
         'brand',p.brand,
         'title',p.title,
         'url',p.url,
         'image_url',p.image_url,
         'presentation',json_object(
           'label',p.title,
           'url',p.url,
           'image_url',p.image_url,
           'link_markdown','['
             || replace(replace(COALESCE(p.title,p.sku),'[','\['),']','\]')
             || '](' || p.url || ')',
           'card_markdown',CASE
             WHEN COALESCE(p.image_url,'')<>'' THEN
               '[![Product: '
               || replace(replace(COALESCE(p.title,p.sku),'[','\['),']','\]')
               || '](' || p.image_url || ')](' || p.url || ')'
               || char(10)
               || '['
               || replace(replace(COALESCE(p.title,p.sku),'[','\['),']','\]')
               || '](' || p.url || ')'
             ELSE
               '['
               || replace(replace(COALESCE(p.title,p.sku),'[','\['),']','\]')
               || '](' || p.url || ')'
           END
         ),
         'price',json_object(
           'value',p.price_value,
           'minimum',p.price_min,
           'maximum',p.price_max,
           'text',p.price_text,
           'call_for_price_text',p.call_for_price_text,
           'show_price',p.show_price,
           'show_call_for_price',p.show_call_for_price
         ),
         'category',p.category,
         'subcategory',p.subcategory,
         'product_type',p.product_type,
         'finish',p.finish,
         'style',p.style,
         'material',p.material,
         'location_rating',p.location_rating,
         'safety_rating',p.safety_rating,
         'ada_compliant',p.ada_compliant,
         'dimmable',p.dimmable,
         'voltage',p.voltage,
         'color_temperature',p.color_temperature,
         'wattage',p.wattage,
         'lumens',p.lumens,
         'number_of_lights',p.number_of_lights,
         'number_of_bulbs',p.number_of_bulbs,
         'specifications',json(COALESCE((
           SELECT json_group_array(json_object(
             'order',json_extract(x.value,'$[0]'),
             'section',json_extract(x.value,'$[1]'),
             'source_layer',json_extract(x.value,'$[2]'),
             'label',json_extract(x.value,'$[3]'),
             'value',json_extract(x.value,'$[4]')
           )) FROM json_each(COALESCE(s.specifications_json,'[]')) AS x
         ),'[]')),
         'conflicts',json(COALESCE((
           SELECT json_group_array(json_object(
             'label',json_extract(x.value,'$[0]'),
             'distinct_values',json_extract(x.value,'$[1]'),
             'values',json(json_extract(x.value,'$[2]'))
           )) FROM json_each(COALESCE(c.conflicts_json,'[]')) AS x
         ),'[]')),
         'options',json(COALESCE((
           SELECT json_group_array(json_object(
             'order',json_extract(x.value,'$[0]'),
             'section',json_extract(x.value,'$[1]'),
             'label',json_extract(x.value,'$[2]'),
             'title',json_extract(x.value,'$[3]'),
             'value',json_extract(x.value,'$[4]'),
             'selected',json_extract(x.value,'$[5]'),
             'url',json_extract(x.value,'$[6]'),
             'presentation',json_object(
               'label',trim(json_extract(x.value,'$[4]')),
               'url',json_extract(x.value,'$[6]'),
               'link_markdown',CASE
                 WHEN COALESCE(json_extract(x.value,'$[6]'),'')<>'' THEN
                   '['
                   || replace(replace(trim(json_extract(x.value,'$[4]')),'[','\['),']','\]')
                   || '](' || json_extract(x.value,'$[6]') || ')'
                 ELSE trim(json_extract(x.value,'$[4]'))
               END
             )
           )) FROM json_each(COALESCE(o.options_json,'[]')) AS x
         ),'[]')),
         'resources',json(COALESCE((
           SELECT json_group_array(json_object(
             'order',json_extract(x.value,'$[0]'),
             'type',json_extract(x.value,'$[1]'),
             'label',json_extract(x.value,'$[2]'),
             'url',json_extract(x.value,'$[3]')
           )) FROM json_each(COALESCE(r.resources_json,'[]')) AS x
         ),'[]'))
       ) AS product_json
FROM products AS p
LEFT JOIN _product_detail_listings AS l USING(product_ref)
LEFT JOIN _product_detail_specifications AS s USING(product_ref)
LEFT JOIN _product_detail_conflicts AS c USING(product_ref)
LEFT JOIN _product_detail_options AS o USING(product_ref)
LEFT JOIN _product_detail_resources AS r USING(product_ref);

UPDATE _presets
SET description='BES lighting staff catalogue read contract.',
    source='sql:bes_staff_read_model',
    sql='-- @name: orient
-- @description: BES lighting staff catalogue read contract
-- @multi: true
-- @query: query_surface
WITH contract(name,grain,purpose,join_key) AS (VALUES
 (''products'',''one resolved row per product'',''Identity, exact URL, deterministic listing, parsed price, and common filter fields'',''product_ref''),
 (''product_specifications'',''one selected-current literal specification occurrence'',''Complete long-tail published specification evidence with normalized search keys'',''product_ref''),
 (''product_search_fts'',''one FTS5 search document per product'',''Broad lexical candidate discovery across catalogue text'',''product_ref''),
 (''product_details'',''one complete staff product evidence row'',''Resolved product plus a clean product_json document with complete specifications, conflicts, options, and resources'',''product_ref'')
)
SELECT
  c.name,c.grain,c.purpose,c.join_key,
  group_concat(p.name || CASE WHEN p.type='''' THEN '''' ELSE '' '' || p.type END,'', '') AS columns
FROM contract AS c
JOIN pragma_table_info(c.name) AS p
GROUP BY c.name,c.grain,c.purpose,c.join_key
ORDER BY c.name;

-- @query: boundaries
SELECT ''product_search_fts supplies recall; products supplies common eligibility, counting, and ordering.'' AS boundary
UNION ALL
SELECT ''Use product_specifications for uncommon or consequential literal requirements.''
UNION ALL
SELECT ''Join only final selected product_ref values to product_details and return stored URLs exactly.'';'
WHERE name='orient';

COMMIT;

ANALYZE products;
ANALYZE product_specifications;
