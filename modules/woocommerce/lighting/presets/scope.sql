-- @name: scope
-- @description: Show the configured WooCommerce Store API capture scope.

SELECT key, value
FROM _meta
WHERE key IN (
    'store_url',
    'per_page',
    'scope',
    'last_pull_at',
    'refresh_module',
    'refresh_interval'
)
ORDER BY key;
