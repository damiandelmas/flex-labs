-- @name: products
-- @description: Browse native XOLogic products
-- @param limit: Maximum rows (default 20)
SELECT product_ref,title,vendor,price,category,subcategory,url
FROM resolved_products
ORDER BY title
LIMIT COALESCE(:limit,20);
