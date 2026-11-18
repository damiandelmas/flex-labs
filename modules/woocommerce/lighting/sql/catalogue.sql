-- @name: catalogue
-- @description: Provider-common WooCommerce catalogue views over native Store API rows.

DROP VIEW IF EXISTS catalogue_products;
CREATE VIEW catalogue_products AS
SELECT p.native_id AS product_id,
       p.slug,
       p.name_raw AS name,
       p.permalink,
       p.product_type,
       p.sku,
       p.stock_status,
       p.is_in_stock,
       p.is_purchasable,
       p.on_sale,
       price.currency_code,
       price.currency_minor_unit,
       COALESCE((SELECT value FROM _meta WHERE key='price_policy'),'public') AS price_visibility,
       CASE WHEN COALESCE((SELECT value FROM _meta WHERE key='price_policy'),'public')='public' THEN price.price_raw END AS price_raw,
       CASE WHEN COALESCE((SELECT value FROM _meta WHERE key='price_policy'),'public')='public' THEN price.regular_price_raw END AS regular_price_raw,
       CASE WHEN COALESCE((SELECT value FROM _meta WHERE key='price_policy'),'public')='public' THEN price.sale_price_raw END AS sale_price_raw,
       CASE WHEN COALESCE((SELECT value FROM _meta WHERE key='price_policy'),'public')='public' THEN price.min_amount_raw END AS min_amount_raw,
       CASE WHEN COALESCE((SELECT value FROM _meta WHERE key='price_policy'),'public')='public' THEN price.max_amount_raw END AS max_amount_raw,
       (SELECT image.source_url FROM woo_product_image_raw image
         WHERE image.native_id=p.native_id ORDER BY image.position LIMIT 1) AS primary_image_url,
       p.permalink AS product_url
FROM woo_product_raw p
LEFT JOIN woo_product_price_raw price ON price.native_id=p.native_id;

DROP VIEW IF EXISTS catalogue_attributes;
CREATE VIEW catalogue_attributes AS
SELECT attribute.native_id AS product_id,
       attribute.position AS attribute_position,
       attribute.attribute_id,
       attribute.name_raw AS attribute_name,
       attribute.taxonomy,
       attribute.has_variations,
       attribute.scalar_value_raw,
       term.position AS term_position,
       term.term_id,
       term.name_raw AS value,
       term.slug AS value_slug
FROM woo_product_attribute_raw attribute
LEFT JOIN woo_product_attribute_term_raw term
  ON term.native_id=attribute.native_id
 AND term.attribute_position=attribute.position;

DROP VIEW IF EXISTS catalogue_terms;
CREATE VIEW catalogue_terms AS
SELECT native_id AS product_id, namespace, position, term_id, name_raw AS name, slug
FROM woo_product_term_raw;

DROP VIEW IF EXISTS catalogue_images;
CREATE VIEW catalogue_images AS
SELECT native_id AS product_id, position, image_id, source_url, thumbnail_url,
       alt_raw AS alt, name_raw AS name
FROM woo_product_image_raw;

DROP VIEW IF EXISTS catalogue_json_values;
CREATE VIEW catalogue_json_values AS
SELECT native_id AS product_id, source_path, parent_path, key_name, ordinal, value_type,
       value_text, value_integer, value_real, value_boolean
FROM woo_product_json_value;

-- WooCommerce-lighting typed projections.
--
-- The catalogue_* views above and the resolved lighting relations below are
-- the single installed WooCommerce-lighting query contract.

DROP VIEW IF EXISTS spec_occurrences;
CREATE VIEW spec_occurrences AS
SELECT occurrence_id,native_id AS product_id,source_path,source_order,precedence,label,value,normalized_label
FROM woo_product_spec_occurrence;

DROP VIEW IF EXISTS resolved_spec_values;
CREATE VIEW resolved_spec_values AS
SELECT occurrence_id,native_id AS product_id,source_path,source_order,precedence,
       label AS raw_label,value AS raw_value,normalized_label,canonical_field,value_kind,
       text_value,numeric_min,numeric_max,is_selectable,boolean_value,unit,parse_status
FROM woo_product_spec_occurrence
WHERE canonical_field IS NOT NULL;

DROP VIEW IF EXISTS spec_conflicts;
CREATE VIEW spec_conflicts AS
SELECT native_id AS product_id,canonical_field,COUNT(DISTINCT lower(value)) AS distinct_values,
       GROUP_CONCAT(DISTINCT value) AS values_seen
FROM woo_product_spec_occurrence
WHERE canonical_field IS NOT NULL
GROUP BY native_id,canonical_field
HAVING COUNT(DISTINCT lower(value)) > 1;

DROP VIEW IF EXISTS resolved_products;
CREATE VIEW resolved_products AS
WITH preferred AS (
  SELECT s.* FROM woo_product_spec_occurrence s
  WHERE s.canonical_field IS NOT NULL
    AND s.precedence=(SELECT MIN(s2.precedence) FROM woo_product_spec_occurrence s2
                      WHERE s2.native_id=s.native_id AND s2.canonical_field=s.canonical_field)
), specs AS (
  SELECT native_id,
    MIN(CASE WHEN canonical_field='voltage' THEN numeric_min END) AS voltage_min,
    MAX(CASE WHEN canonical_field='voltage' THEN numeric_max END) AS voltage_max,
    MIN(CASE WHEN canonical_field='cct' THEN numeric_min END) AS cct_min,
    MAX(CASE WHEN canonical_field='cct' THEN numeric_max END) AS cct_max,
    MAX(CASE WHEN canonical_field='cct' THEN is_selectable END) AS cct_selectable,
    MIN(CASE WHEN canonical_field='wattage' THEN numeric_min END) AS wattage_min,
    MAX(CASE WHEN canonical_field='wattage' THEN numeric_max END) AS wattage_max,
    MAX(CASE WHEN canonical_field='wattage' THEN is_selectable END) AS wattage_selectable,
    MIN(CASE WHEN canonical_field='lumens' THEN numeric_min END) AS lumens_min,
    MAX(CASE WHEN canonical_field='lumens' THEN numeric_max END) AS lumens_max,
    MIN(CASE WHEN canonical_field='cri' THEN numeric_min END) AS cri_min,
    MAX(CASE WHEN canonical_field='cri' THEN numeric_max END) AS cri_max,
    MIN(CASE WHEN canonical_field='ip_rating' THEN numeric_min END) AS ip_rating_min,
    MAX(CASE WHEN canonical_field='ip_rating' THEN numeric_max END) AS ip_rating_max,
    MAX(CASE WHEN canonical_field='fixture_dimmable' THEN boolean_value END) AS fixture_dimmable,
    MAX(CASE WHEN canonical_field='dimming_protocols' THEN text_value END) AS dimming_protocols,
    MAX(CASE WHEN canonical_field='finish' THEN text_value END) AS finish,
    MAX(CASE WHEN canonical_field='style' THEN text_value END) AS style,
    MAX(CASE WHEN canonical_field='material' THEN text_value END) AS material,
    MAX(CASE WHEN canonical_field='location_rating' THEN text_value END) AS location_rating,
    MAX(CASE WHEN canonical_field='safety_rating' THEN text_value END) AS safety_rating
  FROM preferred GROUP BY native_id
)
SELECT p.product_id AS product_ref,p.product_id,e.name_text AS title,e.name_text AS name,p.slug,p.product_url,p.permalink,p.sku,p.product_type,
       p.stock_status,p.is_in_stock,p.is_purchasable,p.on_sale,raw.has_options,raw.is_on_backorder,
       raw.parent_id,raw.raw_json AS source_product_json,p.currency_code,p.currency_minor_unit,
       p.price_visibility,p.price_raw,CASE p.currency_minor_unit WHEN 0 THEN CAST(p.price_raw AS REAL)
         WHEN 1 THEN CAST(p.price_raw AS REAL)/10.0 WHEN 2 THEN CAST(p.price_raw AS REAL)/100.0
         WHEN 3 THEN CAST(p.price_raw AS REAL)/1000.0 END AS price,
       CASE p.currency_minor_unit WHEN 0 THEN CAST(COALESCE(p.min_amount_raw,p.price_raw) AS REAL)
         WHEN 1 THEN CAST(COALESCE(p.min_amount_raw,p.price_raw) AS REAL)/10.0
         WHEN 2 THEN CAST(COALESCE(p.min_amount_raw,p.price_raw) AS REAL)/100.0
         WHEN 3 THEN CAST(COALESCE(p.min_amount_raw,p.price_raw) AS REAL)/1000.0 END AS price_min,
       CASE p.currency_minor_unit WHEN 0 THEN CAST(COALESCE(p.max_amount_raw,p.price_raw) AS REAL)
         WHEN 1 THEN CAST(COALESCE(p.max_amount_raw,p.price_raw) AS REAL)/10.0
         WHEN 2 THEN CAST(COALESCE(p.max_amount_raw,p.price_raw) AS REAL)/100.0
         WHEN 3 THEN CAST(COALESCE(p.max_amount_raw,p.price_raw) AS REAL)/1000.0 END AS price_max,p.primary_image_url,
       e.short_description_text,e.description_text,e.categories_text AS categories,
       e.tags_text AS tags,e.brands_text AS brands,e.attributes_text,e.searchable_text,
       s.voltage_min,s.voltage_max,s.cct_min,s.cct_max,s.cct_selectable,
       s.wattage_min,s.wattage_max,s.wattage_selectable,s.lumens_min,s.lumens_max,
       s.cri_min,s.cri_max,s.ip_rating_min,s.ip_rating_max,s.fixture_dimmable,
       s.dimming_protocols,s.finish,s.style,s.material,s.location_rating,s.safety_rating
FROM catalogue_products p
JOIN woo_product_evidence e ON e.native_id=p.product_id
JOIN woo_product_raw raw ON raw.native_id=p.product_id
LEFT JOIN specs s ON s.native_id=p.product_id;
