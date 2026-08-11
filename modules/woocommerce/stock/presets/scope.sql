-- @name: scope
-- @description: Show the configured WooCommerce Store API capture scope.

SELECT key, value
FROM _meta
WHERE key IN (
    'store_url',
    'per_page',
    'scope',
    'last_pull_at',
    'source_capture_id',
    'source_product_count',
    'refresh_module',
    'refresh_interval'
)
ORDER BY key;
