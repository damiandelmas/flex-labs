-- @name: product-detail
-- @description: Product detail from native Store API tables/views. Params: product_id.
-- @params: product_id

SELECT
    p.product_id,
    p.name,
    p.product_url,
    p.sku,
    p.price_raw,
    p.stock_status,
    (
      SELECT json_group_array(json_object('name', attribute_name, 'value', COALESCE(value, scalar_value_raw)))
      FROM catalogue_attributes a
      WHERE a.product_id = p.product_id
    ) AS attributes_json,
    (
      SELECT json_group_array(json_object('namespace', namespace, 'name', name, 'slug', slug))
      FROM catalogue_terms t
      WHERE t.product_id = p.product_id
    ) AS terms_json,
    (
      SELECT json_group_array(json_object('source_url', source_url, 'alt', alt))
      FROM catalogue_images i
      WHERE i.product_id = p.product_id
    ) AS images_json
FROM catalogue_products p
WHERE p.product_id = :product_id;
