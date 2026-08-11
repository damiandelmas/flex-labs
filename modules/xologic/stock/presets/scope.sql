-- @name: scope
-- @description: Capture edition and native population
-- @multi: true
-- @query: edition
SELECT edition_id,store_name,product_count,source_observation_count,products_sha256
FROM xologic_editions;
-- @query: populations
SELECT 'products' what,count(*) n FROM products
UNION ALL SELECT 'source observations',count(*) FROM xologic_source_observations
UNION ALL SELECT 'content objects',count(*) FROM xologic_content_objects
UNION ALL SELECT 'spec occurrences',count(*) FROM spec_occurrences;
