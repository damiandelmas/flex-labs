-- @name: products
-- @description: WooCommerce products from native Store API tables/views. Params: limit.
-- @params: limit (default: 50)

SELECT
    product_id,
    name,
    product_url,
    sku,
    product_type,
    stock_status,
    is_in_stock,
    currency_code,
    price_raw,
    regular_price_raw,
    sale_price_raw,
    primary_image_url
FROM catalogue_products
ORDER BY name
LIMIT COALESCE(:limit, 50);
