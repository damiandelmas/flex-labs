-- @name: categories
-- @description: Count WooCommerce products by source category.

SELECT
    name,
    slug,
    COUNT(DISTINCT product_id) AS products
FROM catalogue_terms
WHERE namespace = 'category'
GROUP BY name, slug
ORDER BY products DESC, name
LIMIT 100;
