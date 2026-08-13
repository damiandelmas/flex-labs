-- Derived product retrieval surface for structured XOLogic catalogues.
--
-- Native XOLogic relations remain authoritative. This relation gathers their
-- searchable vocabulary at product identity, and products_fts is a disposable
-- FTS5 index over that projection.

DROP TRIGGER IF EXISTS product_search_documents_ai;
DROP TRIGGER IF EXISTS product_search_documents_ad;
DROP TRIGGER IF EXISTS product_search_documents_au;
DROP TABLE IF EXISTS product_dossier_payloads;
DROP TABLE IF EXISTS product_identifiers;
DROP VIEW IF EXISTS product_dossiers;
DROP TABLE IF EXISTS products_fts;
DROP TABLE IF EXISTS product_search_documents;

CREATE TABLE product_search_documents (
  product_ref TEXT PRIMARY KEY REFERENCES products(product_ref),
  title TEXT NOT NULL,
  identifiers TEXT NOT NULL,
  vendor TEXT NOT NULL,
  taxonomy TEXT NOT NULL,
  specifications TEXT NOT NULL,
  description TEXT NOT NULL
);

CREATE TABLE product_identifiers (
  product_ref TEXT NOT NULL REFERENCES products(product_ref),
  identifier_kind TEXT NOT NULL,
  identifier_value TEXT NOT NULL,
  normalized_identifier TEXT NOT NULL,
  PRIMARY KEY(product_ref,identifier_kind,identifier_value)
);

INSERT INTO product_identifiers
  (product_ref,identifier_kind,identifier_value,normalized_identifier)
SELECT product_ref,'sku',trim(sku),lower(trim(sku))
FROM products WHERE trim(COALESCE(sku,''))<>''
UNION
SELECT product_ref,'partnumber',trim(partnumber),lower(trim(partnumber))
FROM products WHERE trim(COALESCE(partnumber,''))<>''
UNION
SELECT product_ref,'gtin',trim(gtin),lower(trim(gtin))
FROM products WHERE trim(COALESCE(gtin,''))<>''
UNION
SELECT product_ref,'handle',trim(handle),lower(trim(handle))
FROM products WHERE trim(COALESCE(handle,''))<>'';

CREATE INDEX ix_product_identifiers_lookup
  ON product_identifiers(normalized_identifier,identifier_kind,product_ref);

INSERT INTO product_search_documents(
  product_ref,title,identifiers,vendor,taxonomy,specifications,description
)
SELECT
  p.product_ref,
  p.title,
  trim(
    COALESCE(p.sku,'') || ' ' ||
    COALESCE(p.partnumber,'') || ' ' ||
    COALESCE(p.gtin,'') || ' ' ||
    COALESCE(p.handle,'')
  ),
  COALESCE(p.vendor,''),
  COALESCE((
    SELECT group_concat(label,' | ')
    FROM (
      SELECT t.label
      FROM taxonomy_paths AS t
      WHERE t.product_ref=p.product_ref
      ORDER BY t.depth,lower(t.label),t.label
    )
  ),''),
  COALESCE((
    SELECT group_concat(label || ': ' || value,' | ')
    FROM (
      SELECT DISTINCT s.label,s.value
      FROM spec_occurrences AS s
      WHERE s.product_ref=p.product_ref
      ORDER BY lower(s.label),lower(s.value),s.label,s.value
    )
  ),''),
  COALESCE(p.body_html,'')
FROM products AS p
ORDER BY p.source_order,p.product_ref;

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

CREATE TRIGGER product_search_documents_ai
AFTER INSERT ON product_search_documents BEGIN
  INSERT INTO products_fts(
    rowid,product_ref,title,identifiers,vendor,taxonomy,specifications,description
  ) VALUES (
    new.rowid,new.product_ref,new.title,new.identifiers,new.vendor,
    new.taxonomy,new.specifications,new.description
  );
END;

CREATE TRIGGER product_search_documents_ad
AFTER DELETE ON product_search_documents BEGIN
  INSERT INTO products_fts(
    products_fts,rowid,product_ref,title,identifiers,vendor,taxonomy,
    specifications,description
  ) VALUES (
    'delete',old.rowid,old.product_ref,old.title,old.identifiers,old.vendor,
    old.taxonomy,old.specifications,old.description
  );
END;

CREATE TRIGGER product_search_documents_au
AFTER UPDATE ON product_search_documents BEGIN
  INSERT INTO products_fts(
    products_fts,rowid,product_ref,title,identifiers,vendor,taxonomy,
    specifications,description
  ) VALUES (
    'delete',old.rowid,old.product_ref,old.title,old.identifiers,old.vendor,
    old.taxonomy,old.specifications,old.description
  );
  INSERT INTO products_fts(
    rowid,product_ref,title,identifiers,vendor,taxonomy,specifications,description
  ) VALUES (
    new.rowid,new.product_ref,new.title,new.identifiers,new.vendor,
    new.taxonomy,new.specifications,new.description
  );
END;

-- Generic product-return contract. Search, filtering, and ranking may operate
-- over narrower relations internally, but every product row handed to an agent
-- is expanded through this view. Native source bodies remain stored once in
-- xologic_content_objects and are recoverable through the content hashes and
-- descriptors included below.
CREATE VIEW product_dossiers AS
SELECT
  r.product_ref,
  r.url,
  r.sku,
  r.partnumber,
  r.gtin,
  r.title,
  r.vendor,
  r.image_url,
  r.category,
  r.subcategory,
  r.price_min,
  r.price_max,
  r.product_type,
  r.finish,
  r.style,
  r.material,
  r.location_rating,
  r.safety_rating,
  r.voltage_min,
  r.voltage_max,
  r.cct_min,
  r.cct_max,
  r.cct_selectable,
  r.wattage_min,
  r.wattage_max,
  r.wattage_selectable,
  r.max_wattage_min,
  r.max_wattage_max,
  r.bulb_wattage_min,
  r.bulb_wattage_max,
  r.lumens_min,
  r.lumens_max,
  r.delivered_lumens_min,
  r.delivered_lumens_max,
  r.light_source_lumens_min,
  r.light_source_lumens_max,
  r.cri_min,
  r.cri_max,
  r.bulb_cri_min,
  r.bulb_cri_max,
  r.ip_rating_min,
  r.ip_rating_max,
  r.fixture_dimmable,
  r.bulb_dimmable,
  r.dimming_protocols,
  r.minimum_dim_percent_min,
  r.minimum_dim_percent_max,
  r.preferred_specs_json,
  COALESCE((
    SELECT json_group_array(json_array(q.depth,q.label))
    FROM (
      SELECT t.depth,t.label
      FROM taxonomy_paths AS t
      WHERE t.product_ref=r.product_ref
      ORDER BY t.depth,lower(t.label),t.label
    ) AS q
  ),'[]') AS taxonomy_json,
  COALESCE((
    SELECT json_group_array(json_array(
      q.source_layer,q.label,q.value,q.visible,q.occurrence_count
    ))
    FROM (
      SELECT s.source_layer,s.label,s.value,s.visible,count(*) AS occurrence_count
      FROM spec_occurrences AS s
      WHERE s.product_ref=r.product_ref
      GROUP BY s.source_layer,s.label,s.value,s.visible
      ORDER BY lower(s.label),lower(s.value),s.source_layer,s.visible
    ) AS q
  ),'[]') AS specifications_json,
  COALESCE((
    SELECT json_group_array(json_array(
      q.label,q.distinct_values,q.values_seen
    ))
    FROM (
      SELECT c.label,c.distinct_values,c.values_seen
      FROM spec_conflicts AS c
      WHERE c.product_ref=r.product_ref
      ORDER BY lower(c.label),c.label
    ) AS q
  ),'[]') AS conflicts_json,
  COALESCE((
    SELECT json_group_array(json_array(
      q.kind,q.url,q.occurrence_count
    ))
    FROM (
      SELECT
        l.kind,
        CASE
          WHEN instr(l.url,'#')>0 THEN substr(l.url,1,instr(l.url,'#')-1)
          ELSE l.url
        END AS url,
        count(*) AS occurrence_count
      FROM product_links AS l
      WHERE l.product_ref=r.product_ref AND l.kind IN ('option_for','variant')
      GROUP BY l.kind,
        CASE WHEN instr(l.url,'#')>0 THEN substr(l.url,1,instr(l.url,'#')-1) ELSE l.url END
      ORDER BY l.kind,url
    ) AS q
  ),'[]') AS variants_json,
  COALESCE((
    SELECT json_group_array(json_array(
      q.kind,q.url,q.occurrence_count
    ))
    FROM (
      SELECT
        x.kind,x.url,
        count(*) AS occurrence_count
      FROM product_resources AS x
      WHERE x.product_ref=r.product_ref AND x.kind='document'
      GROUP BY x.kind,x.url
      ORDER BY x.kind,x.url
    ) AS q
  ),'[]') AS documents_json
FROM resolved_products AS r;

-- Exact serialized size for deterministic complete-product batching. The JSON
-- is a derived transport measurement, not another product authority.
CREATE TABLE product_dossier_payloads AS
SELECT
  d.product_ref,
  json_object(
    'product_ref',d.product_ref,'url',d.url,'sku',d.sku,
    'partnumber',d.partnumber,'gtin',d.gtin,'title',d.title,
    'vendor',d.vendor,'image_url',d.image_url,'category',d.category,
    'subcategory',d.subcategory,'price_min',d.price_min,
    'price_max',d.price_max,'product_type',d.product_type,
    'finish',d.finish,'style',d.style,'material',d.material,
    'location_rating',d.location_rating,'safety_rating',d.safety_rating,
    'voltage_min',d.voltage_min,'voltage_max',d.voltage_max,
    'cct_min',d.cct_min,'cct_max',d.cct_max,
    'cct_selectable',d.cct_selectable,'wattage_min',d.wattage_min,
    'wattage_max',d.wattage_max,'wattage_selectable',d.wattage_selectable,
    'max_wattage_min',d.max_wattage_min,'max_wattage_max',d.max_wattage_max,
    'bulb_wattage_min',d.bulb_wattage_min,
    'bulb_wattage_max',d.bulb_wattage_max,
    'lumens_min',d.lumens_min,'lumens_max',d.lumens_max,
    'delivered_lumens_min',d.delivered_lumens_min,
    'delivered_lumens_max',d.delivered_lumens_max,
    'light_source_lumens_min',d.light_source_lumens_min,
    'light_source_lumens_max',d.light_source_lumens_max,
    'cri_min',d.cri_min,'cri_max',d.cri_max,
    'bulb_cri_min',d.bulb_cri_min,'bulb_cri_max',d.bulb_cri_max,
    'ip_rating_min',d.ip_rating_min,'ip_rating_max',d.ip_rating_max,
    'fixture_dimmable',d.fixture_dimmable,'bulb_dimmable',d.bulb_dimmable,
    'dimming_protocols',d.dimming_protocols,
    'minimum_dim_percent_min',d.minimum_dim_percent_min,
    'minimum_dim_percent_max',d.minimum_dim_percent_max,
    'preferred_specs',json(d.preferred_specs_json),
    'taxonomy',json(d.taxonomy_json),
    'specifications',json(d.specifications_json),
    'conflicts',json(d.conflicts_json),
    'variants',json(d.variants_json),
    'documents',json(d.documents_json)
  ) AS dossier_json
FROM product_dossiers AS d;

CREATE UNIQUE INDEX ix_product_dossier_payloads_product
  ON product_dossier_payloads(product_ref);
