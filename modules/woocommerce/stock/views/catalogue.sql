-- @name: catalogue
-- @description: Raw WooCommerce catalogue views.

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
       price.price_raw,
       price.regular_price_raw,
       price.sale_price_raw,
       price.min_amount_raw,
       price.max_amount_raw,
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
