-- @name: orient
-- @description: Map of the WooCommerce lighting catalogue
-- @multi: true

-- @query: identity
WITH metadata AS (
  SELECT
    max(CASE WHEN key='description' THEN value END) AS description,
    max(CASE WHEN key='store_url' THEN value END) AS store_url,
    max(CASE WHEN key='provider' THEN value END) AS provider,
    max(CASE WHEN key='profile' THEN value END) AS profile,
    max(CASE WHEN key='authority' THEN value END) AS authority
  FROM _meta
)
SELECT metadata.*,capture_id,captured_at,captured_count
FROM metadata CROSS JOIN woo_capture;

-- @query: shape
SELECT 'products' AS what,count(*) AS n FROM woo_product_raw
UNION ALL SELECT 'attributes and options',count(*) FROM catalogue_attributes
UNION ALL SELECT 'taxonomy terms',count(*) FROM catalogue_terms
UNION ALL SELECT 'images',count(*) FROM catalogue_images
UNION ALL SELECT 'specification occurrences',count(*) FROM spec_occurrences
UNION ALL SELECT 'identifiers',count(*) FROM product_identifiers
UNION ALL SELECT 'search documents',count(*) FROM product_search_documents
UNION ALL SELECT 'complete dossiers',count(*) FROM product_dossiers;

-- @query: relations
WITH public(name,role) AS (
  VALUES
    ('resolved_products','one row per product for ordinary filtering and ranking'),
    ('product_identifiers','native id, SKU, slug, and exact product URL lookup'),
    ('products_fts','broad text discovery over products'),
    ('product_dossiers','complete product rows returned to Felix'),
    ('product_dossier_payloads','serialized dossier size for bounded result batches'),
    ('spec_occurrences','exact published labels and values'),
    ('resolved_spec_values','typed interpretations with source paths'),
    ('spec_conflicts','repeated interpreted fields with differing values'),
    ('catalogue_attributes','published attributes and option terms'),
    ('catalogue_terms','published categories, tags, and brands'),
    ('catalogue_images','published product images'),
    ('catalogue_json_values','arbitrary Store API scalar values'),
    ('woo_product_raw','complete canonical Store API product JSON'),
    ('woo_native_document','exact Store API page and product bytes')
)
SELECT public.name AS relation,m.type AS kind,
       group_concat(p.name,', ') AS columns,public.role
FROM public
JOIN sqlite_master m ON m.name=public.name
JOIN pragma_table_info(public.name) p
GROUP BY public.name,m.type,public.role
ORDER BY public.name;

-- @query: boundaries
SELECT 'woo_native_document and woo_product_raw preserve the source; every other product relation is a rebuildable SQL interpretation.' AS boundary
UNION ALL SELECT 'resolved scalar fields may prefer one value; spec_occurrences and dossier arrays preserve all published values and conflicts.'
UNION ALL SELECT 'price_visibility governs price exposure; stock status, in-stock, backorder, and purchasability are separate source facts.'
UNION ALL SELECT 'product_url is the exact Store API permalink; WooCommerce brand labels are not verified manufacturer identity.';
