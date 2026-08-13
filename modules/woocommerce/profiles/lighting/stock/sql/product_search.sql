-- WooCommerce-lighting query surfaces.
--
-- The woo_* relations remain authoritative. These relations are ordinary SQL
-- transformations over that source and are rebuilt with each atomic refresh.

DROP VIEW IF EXISTS product_dossier_payloads;
DROP VIEW IF EXISTS product_dossiers;
DROP TABLE IF EXISTS products_fts;
DROP TABLE IF EXISTS product_search_documents;
DROP TABLE IF EXISTS product_identifiers;

CREATE TABLE product_identifiers (
  product_ref TEXT NOT NULL REFERENCES woo_product_raw(native_id),
  identifier_kind TEXT NOT NULL,
  identifier_value TEXT NOT NULL,
  normalized_identifier TEXT NOT NULL,
  PRIMARY KEY(product_ref,identifier_kind,identifier_value)
);

INSERT INTO product_identifiers(
  product_ref,identifier_kind,identifier_value,normalized_identifier
)
SELECT native_id,'native_id',trim(native_id),lower(trim(native_id))
FROM woo_product_raw
WHERE trim(native_id)<>''
UNION
SELECT native_id,'sku',trim(sku),lower(trim(sku))
FROM woo_product_raw
WHERE trim(COALESCE(sku,''))<>''
UNION
SELECT native_id,'slug',trim(slug),lower(trim(slug))
FROM woo_product_raw
WHERE trim(COALESCE(slug,''))<>''
UNION
SELECT native_id,'product_url',trim(permalink),lower(trim(permalink))
FROM woo_product_raw
WHERE trim(COALESCE(permalink,''))<>'';

CREATE INDEX ix_product_identifiers_lookup
  ON product_identifiers(normalized_identifier,identifier_kind,product_ref);

CREATE TABLE product_search_documents (
  product_ref TEXT PRIMARY KEY REFERENCES woo_product_raw(native_id),
  title TEXT NOT NULL,
  identifiers TEXT NOT NULL,
  vendor TEXT NOT NULL,
  taxonomy TEXT NOT NULL,
  specifications TEXT NOT NULL,
  description TEXT NOT NULL
);

INSERT INTO product_search_documents(
  product_ref,title,identifiers,vendor,taxonomy,specifications,description
)
SELECT
  e.native_id,
  e.name_text,
  COALESCE((
    SELECT group_concat(identifier_value,' | ')
    FROM (
      SELECT i.identifier_value
      FROM product_identifiers i
      WHERE i.product_ref=e.native_id
      ORDER BY i.identifier_kind,lower(i.identifier_value),i.identifier_value
    )
  ),''),
  COALESCE(e.brands_text,''),
  trim(COALESCE(e.categories_text,'') || ' ' ||
       COALESCE(e.tags_text,'') || ' ' ||
       COALESCE(e.brands_text,'')),
  COALESCE((
    SELECT group_concat(label || ': ' || value,' | ')
    FROM (
      SELECT DISTINCT s.label,s.value
      FROM spec_occurrences s
      WHERE s.product_id=e.native_id
      ORDER BY lower(s.label),lower(s.value),s.label,s.value
    )
  ),''),
  trim(COALESCE(e.short_description_text,'') || ' ' ||
       COALESCE(e.description_text,'') || ' ' ||
       COALESCE(e.attributes_text,''))
FROM woo_product_evidence e
ORDER BY e.native_id;

CREATE VIRTUAL TABLE products_fts USING fts5(
  product_ref UNINDEXED,
  title,
  identifiers,
  vendor,
  taxonomy,
  specifications,
  description,
  content='product_search_documents',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

INSERT INTO products_fts(products_fts) VALUES('rebuild');

CREATE VIEW product_dossiers AS
SELECT
  r.product_ref,
  r.product_id,
  r.product_url,
  r.title,
  r.name,
  r.slug,
  r.sku,
  r.product_type,
  r.parent_id,
  r.has_options,
  r.stock_status,
  r.is_in_stock,
  r.is_on_backorder,
  r.is_purchasable,
  r.on_sale,
  r.price_visibility,
  r.currency_code,
  r.currency_minor_unit,
  r.price,
  r.price_min,
  r.price_max,
  r.primary_image_url,
  r.short_description_text,
  r.description_text,
  r.categories,
  r.tags,
  r.brands,
  r.attributes_text,
  r.voltage_min,r.voltage_max,
  r.cct_min,r.cct_max,r.cct_selectable,
  r.wattage_min,r.wattage_max,r.wattage_selectable,
  r.lumens_min,r.lumens_max,
  r.cri_min,r.cri_max,
  r.ip_rating_min,r.ip_rating_max,
  r.fixture_dimmable,r.dimming_protocols,
  r.finish,r.style,r.material,r.location_rating,r.safety_rating,
  raw.capture_id,
  raw.product_document_id,
  raw.page_document_id,
  raw.source_page,
  raw.source_position,
  raw.raw_sha256,
  COALESCE((
    SELECT json_group_array(json_object(
      'namespace',q.namespace,'position',q.position,'id',q.term_id,
      'name',q.name,'slug',q.slug
    ))
    FROM (
      SELECT t.namespace,t.position,t.term_id,t.name,t.slug
      FROM catalogue_terms t
      WHERE t.product_id=r.product_ref
      ORDER BY t.namespace,t.position
    ) q
  ),'[]') AS taxonomy_json,
  COALESCE((
    SELECT json_group_array(json_object(
      'position',q.attribute_position,'id',q.attribute_id,
      'name',q.attribute_name,'taxonomy',q.taxonomy,
      'has_variations',q.has_variations,'scalar_value',q.scalar_value_raw,
      'term_position',q.term_position,'term_id',q.term_id,
      'value',q.value,'value_slug',q.value_slug
    ))
    FROM (
      SELECT a.*
      FROM catalogue_attributes a
      WHERE a.product_id=r.product_ref
      ORDER BY a.attribute_position,a.term_position
    ) q
  ),'[]') AS attributes_json,
  COALESCE((
    SELECT json_group_array(json_object(
      'occurrence_id',q.occurrence_id,'source_path',q.source_path,
      'source_order',q.source_order,'precedence',q.precedence,
      'label',q.label,'value',q.value,'canonical_field',q.canonical_field,
      'value_kind',q.value_kind,'text_value',q.text_value,
      'numeric_min',q.numeric_min,'numeric_max',q.numeric_max,
      'is_selectable',q.is_selectable,'boolean_value',q.boolean_value,
      'unit',q.unit,'parse_status',q.parse_status
    ))
    FROM (
      SELECT s.occurrence_id,s.source_path,s.source_order,s.precedence,
             s.label,s.value,s.canonical_field,
             s.value_kind,s.text_value,s.numeric_min,s.numeric_max,
             s.is_selectable,s.boolean_value,s.unit,s.parse_status
      FROM woo_product_spec_occurrence s
      WHERE s.native_id=r.product_ref
      ORDER BY s.precedence,s.source_order,s.occurrence_id
    ) q
  ),'[]') AS specifications_json,
  COALESCE((
    SELECT json_group_array(json_object(
      'canonical_field',q.canonical_field,
      'distinct_values',q.distinct_values,'values_seen',q.values_seen
    ))
    FROM (
      SELECT c.*
      FROM spec_conflicts c
      WHERE c.product_id=r.product_ref
      ORDER BY c.canonical_field
    ) q
  ),'[]') AS conflicts_json,
  COALESCE((
    SELECT json_group_array(json_object(
      'position',q.position,'id',q.image_id,'source_url',q.source_url,
      'thumbnail_url',q.thumbnail_url,'alt',q.alt,'name',q.name
    ))
    FROM (
      SELECT i.*
      FROM catalogue_images i
      WHERE i.product_id=r.product_ref
      ORDER BY i.position
    ) q
  ),'[]') AS images_json,
  COALESCE(json_extract(raw.raw_json,'$.variations'),'[]') AS variations_json
FROM resolved_products r
JOIN woo_product_raw raw ON raw.native_id=r.product_ref;

-- Exact serialized size for complete-product batching. This transport payload
-- intentionally contains only policy-safe dossier fields, never raw price JSON
-- or the full provider body.
CREATE VIEW product_dossier_payloads AS
SELECT
  d.product_ref,
  json_object(
    'product_ref',d.product_ref,'product_id',d.product_id,
    'product_url',d.product_url,'title',d.title,'name',d.name,
    'slug',d.slug,'sku',d.sku,'product_type',d.product_type,
    'parent_id',d.parent_id,'has_options',d.has_options,
    'stock_status',d.stock_status,'is_in_stock',d.is_in_stock,
    'is_on_backorder',d.is_on_backorder,'is_purchasable',d.is_purchasable,
    'on_sale',d.on_sale,'price_visibility',d.price_visibility,
    'currency_code',d.currency_code,'currency_minor_unit',d.currency_minor_unit,
    'price',d.price,'price_min',d.price_min,'price_max',d.price_max,
    'primary_image_url',d.primary_image_url,
    'short_description',d.short_description_text,
    'description',d.description_text,'categories',d.categories,
    'tags',d.tags,'brands',d.brands,'attributes_text',d.attributes_text,
    'voltage_min',d.voltage_min,'voltage_max',d.voltage_max,
    'cct_min',d.cct_min,'cct_max',d.cct_max,'cct_selectable',d.cct_selectable,
    'wattage_min',d.wattage_min,'wattage_max',d.wattage_max,
    'wattage_selectable',d.wattage_selectable,
    'lumens_min',d.lumens_min,'lumens_max',d.lumens_max,
    'cri_min',d.cri_min,'cri_max',d.cri_max,
    'ip_rating_min',d.ip_rating_min,'ip_rating_max',d.ip_rating_max,
    'fixture_dimmable',d.fixture_dimmable,
    'dimming_protocols',d.dimming_protocols,'finish',d.finish,
    'style',d.style,'material',d.material,
    'location_rating',d.location_rating,'safety_rating',d.safety_rating,
    'capture_id',d.capture_id,'product_document_id',d.product_document_id,
    'page_document_id',d.page_document_id,'source_page',d.source_page,
    'source_position',d.source_position,'raw_sha256',d.raw_sha256,
    'taxonomy',json(d.taxonomy_json),'attributes',json(d.attributes_json),
    'specifications',json(d.specifications_json),
    'conflicts',json(d.conflicts_json),'images',json(d.images_json),
    'variations',json(d.variations_json)
  ) AS dossier_json
FROM product_dossiers d;
