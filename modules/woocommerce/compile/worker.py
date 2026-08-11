#!/usr/bin/env python3
"""WooCommerce Store API -> native SQLite.

This module is deliberately only a Store API shim. It keeps exact public
WooCommerce response bytes, raw Woo tables, and SQL catalogue views in one
native SQLite DB. It does not normalize through
Factory product mappers, Shopify-shaped records, LLM enrichment, webhooks, or
unrelated deployment behavior.

Authority order:

    exact Store API response bytes
        -> transient same-schema SQLite file
        -> atomic native SQLite cell replacement

The transient file keeps the exact response bytes for every paginated API page
and stores each complete product object in raw Woo tables before it replaces the
registered SQLite file.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import ceil
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx


PRODUCTS_PATH = "/wp-json/wc/store/v1/products"
FORMAT = "woocommerce-native-raw-module/v1"
DEFAULT_REFRESH_INTERVAL = 6 * 60 * 60
RAW_TABLES = (
    "woo_capture",
    "woo_native_document",
    "woo_product_raw",
    "woo_product_price_raw",
    "woo_product_attribute_raw",
    "woo_product_attribute_term_raw",
    "woo_product_term_raw",
    "woo_product_image_raw",
    "woo_product_json_value",
)
HEADER_ALLOWLIST = (
    "content-type",
    "etag",
    "last-modified",
    "link",
    "x-wp-total",
    "x-wp-totalpages",
)
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}

SELF_ORIENT_SQL = """-- @name: self-orient
-- @description: WooCommerce native Store API orientation — capture, raw fields, query views, and integrity
-- @multi: true

-- @query: about
SELECT value AS description FROM _meta WHERE key='description';

-- @query: capture
SELECT capture_id,store_url,endpoint,captured_at,expected_count,captured_count,expected_pages,
       manifest_sha256,product_ids_sha256
FROM woo_capture;

-- @query: shape
SELECT 'products' AS what,COUNT(*) AS n FROM woo_product_raw
UNION ALL SELECT 'attributes',COUNT(*) FROM woo_product_attribute_raw
UNION ALL SELECT 'attribute terms',COUNT(*) FROM woo_product_attribute_term_raw
UNION ALL SELECT 'taxonomy terms',COUNT(*) FROM woo_product_term_raw
UNION ALL SELECT 'images',COUNT(*) FROM woo_product_image_raw
UNION ALL SELECT 'native documents',COUNT(*) FROM woo_native_document
UNION ALL SELECT 'scalar values',COUNT(*) FROM woo_product_json_value;

-- @query: columns
SELECT 'catalogue_products' AS surface,
       'product_id, name, product_url, permalink, price_raw, regular_price_raw, sale_price_raw, currency_code, stock_status, is_in_stock, primary_image_url' AS columns,
       'One row per WooCommerce product. Start product discovery here and return name plus product_url when presenting an item.' AS note
UNION ALL
SELECT 'catalogue_attributes',
       'product_id, attribute_name, value, value_slug, scalar_value_raw',
       'Join to catalogue_products on product_id for raw specifications. Attribute values are exact source text, not normalized numeric fields.'
UNION ALL
SELECT 'catalogue_terms',
       'product_id, namespace, name, slug',
       'Join to catalogue_products on product_id for WooCommerce categories, tags, and other taxonomy terms.'
UNION ALL
SELECT 'catalogue_images',
       'product_id, source_url, thumbnail_url, alt, name',
       'Published product-image records.'
UNION ALL
SELECT 'catalogue_json_values',
       'product_id, source_path, value_text, value_integer, value_real, value_boolean',
       'Lossless scalar Store API fields not otherwise modeled.';

-- @query: cell_docs
SELECT 'raw-woocommerce-query-contract' AS section,
       'This is a lossless native WooCommerce Store API catalogue. Exact page bytes and product JSON are authoritative. Query catalogue_products, then join catalogue_attributes, catalogue_terms, catalogue_images, or catalogue_json_values using product_id. Product links are catalogue_products.product_url. For a product category/name search, use lower(p.name) LIKE or join catalogue_terms. For specifications, use a.attribute_name and a.value exactly as the Store API provides it. Do not infer certifications, compatibility, finish/variant families, or identity unless the raw source explicitly establishes it. If an attribute is absent or conflicting, say the catalogue does not establish it.' AS content;

-- @query: query_surface
SELECT 'view' AS kind,m.name AS name,GROUP_CONCAT(p.name,', ') AS columns
FROM sqlite_master m JOIN pragma_table_info(m.name) p
WHERE m.type='view' AND m.name LIKE 'catalogue_%'
GROUP BY m.name
UNION ALL
SELECT 'raw table','woo_product_raw','one full Store API product object per native product id'
UNION ALL
SELECT 'raw table','woo_native_document','exact paginated Store API response bytes plus canonical product object bytes'
UNION ALL
SELECT 'raw table','woo_product_json_value','every scalar from arbitrary Store API keys, retained by source path'
ORDER BY 1,2;

-- @query: examples
SELECT 'products by catalogue category' AS purpose,
       'SELECT p.name,p.permalink FROM catalogue_products p JOIN catalogue_terms t ON t.product_id=p.product_id WHERE t.namespace=''category'' AND t.name=:category;' AS sql
UNION ALL
SELECT 'products with a supplied native attribute value',
       'SELECT p.name,p.permalink FROM catalogue_products p JOIN catalogue_attributes a ON a.product_id=p.product_id WHERE a.attribute_name=:attribute AND a.value=:value;'
UNION ALL
SELECT 'inspect an unmodeled Store API field',
       'SELECT product_id,source_path,value_type,value_text,value_integer,value_real,value_boolean FROM catalogue_json_values WHERE source_path=:path;';
"""

# The local Flex engine resolves @orient through `self-orient` when present.
# Keep `orient` as a direct alias for clients that ask for it by exact name.
ORIENT_SQL = SELF_ORIENT_SQL.replace("-- @name: self-orient", "-- @name: orient", 1)


class CaptureError(RuntimeError):
    """The public Store API could not provide a sealed source population."""


class IntegrityError(RuntimeError):
    """A bundle or native SQLite database disagrees with its evidence."""


@dataclass(frozen=True)
class StorePage:
    number: int
    url: str
    fetched_at: str
    headers: dict[str, str]
    body: bytes

    @property
    def sha256(self) -> str:
        return _sha256_bytes(self.body)

    def products(self) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"Store API page {self.number} is not valid UTF-8 JSON") from exc
        if not isinstance(parsed, list) or not all(isinstance(p, dict) for p in parsed):
            raise IntegrityError(f"Store API page {self.number} is not a product array")
        return parsed


@dataclass(frozen=True)
class StoreCapture:
    store_url: str
    endpoint: str
    captured_at: str
    per_page: int
    expected_count: int
    expected_pages: int
    pages: tuple[StorePage, ...]

    @property
    def products(self) -> list[dict[str, Any]]:
        return [product for page in self.pages for product in page.products()]

    @property
    def manifest(self) -> dict[str, Any]:
        page_rows = [
            {
                "number": page.number,
                "url": page.url,
                "fetched_at": page.fetched_at,
                "headers": page.headers,
                "sha256": page.sha256,
                "bytes": len(page.body),
                "product_count": len(page.products()),
            }
            for page in self.pages
        ]
        product_ids = [_native_id(product) for product in self.products]
        return {
            "format": FORMAT,
            "store_url": self.store_url,
            "endpoint": self.endpoint,
            "captured_at": self.captured_at,
            "per_page": self.per_page,
            "expected_count": self.expected_count,
            "expected_pages": self.expected_pages,
            "captured_count": len(product_ids),
            "product_ids_sha256": _sha256_text(_canonical_json(product_ids)),
            "pages": page_rows,
        }

    @property
    def manifest_sha256(self) -> str:
        return _sha256_text(_canonical_json(self.manifest))

    @property
    def capture_id(self) -> str:
        host = urlsplit(self.store_url).netloc.lower().replace(".", "-")
        return f"woo-{host}-{self.manifest_sha256[:16]}"


RAW_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE woo_capture (
  capture_id TEXT PRIMARY KEY,
  format TEXT NOT NULL,
  store_url TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  per_page INTEGER NOT NULL,
  expected_count INTEGER NOT NULL,
  expected_pages INTEGER NOT NULL,
  captured_count INTEGER NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  product_ids_sha256 TEXT NOT NULL
);

CREATE TABLE woo_native_document (
  document_id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL REFERENCES woo_capture(capture_id),
  kind TEXT NOT NULL CHECK(kind IN ('store_api_page', 'store_api_product')),
  ordinal INTEGER NOT NULL,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  content_type TEXT,
  headers_json TEXT,
  body BLOB NOT NULL,
  body_sha256 TEXT NOT NULL,
  UNIQUE(capture_id, kind, ordinal),
  UNIQUE(capture_id, kind, body_sha256)
);

CREATE TABLE woo_product_raw (
  native_id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL REFERENCES woo_capture(capture_id),
  product_document_id TEXT NOT NULL REFERENCES woo_native_document(document_id),
  page_document_id TEXT NOT NULL REFERENCES woo_native_document(document_id),
  source_page INTEGER NOT NULL,
  source_position INTEGER NOT NULL,
  slug TEXT,
  name_raw TEXT,
  permalink TEXT,
  product_type TEXT,
  sku TEXT,
  parent_id TEXT,
  short_description_raw TEXT,
  description_raw TEXT,
  price_html_raw TEXT,
  average_rating_raw TEXT,
  review_count INTEGER,
  has_options INTEGER CHECK(has_options IN (0, 1) OR has_options IS NULL),
  is_in_stock INTEGER CHECK(is_in_stock IN (0, 1) OR is_in_stock IS NULL),
  is_on_backorder INTEGER CHECK(is_on_backorder IN (0, 1) OR is_on_backorder IS NULL),
  is_password_protected INTEGER CHECK(is_password_protected IN (0, 1) OR is_password_protected IS NULL),
  is_purchasable INTEGER CHECK(is_purchasable IN (0, 1) OR is_purchasable IS NULL),
  on_sale INTEGER CHECK(on_sale IN (0, 1) OR on_sale IS NULL),
  sold_individually INTEGER CHECK(sold_individually IN (0, 1) OR sold_individually IS NULL),
  stock_status TEXT,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL
);

CREATE TABLE woo_product_price_raw (
  native_id TEXT PRIMARY KEY REFERENCES woo_product_raw(native_id),
  currency_code TEXT,
  currency_symbol TEXT,
  currency_minor_unit INTEGER,
  price_raw TEXT,
  regular_price_raw TEXT,
  sale_price_raw TEXT,
  min_amount_raw TEXT,
  max_amount_raw TEXT,
  price_json TEXT NOT NULL,
  price_sha256 TEXT NOT NULL
);

CREATE TABLE woo_product_attribute_raw (
  native_id TEXT NOT NULL REFERENCES woo_product_raw(native_id),
  position INTEGER NOT NULL,
  attribute_id TEXT,
  name_raw TEXT,
  taxonomy TEXT,
  has_variations INTEGER CHECK(has_variations IN (0, 1) OR has_variations IS NULL),
  scalar_value_raw TEXT,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  PRIMARY KEY(native_id, position)
);

CREATE TABLE woo_product_attribute_term_raw (
  native_id TEXT NOT NULL,
  attribute_position INTEGER NOT NULL,
  position INTEGER NOT NULL,
  term_id TEXT,
  name_raw TEXT,
  slug TEXT,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  PRIMARY KEY(native_id, attribute_position, position),
  FOREIGN KEY(native_id, attribute_position)
    REFERENCES woo_product_attribute_raw(native_id, position)
);

CREATE TABLE woo_product_term_raw (
  native_id TEXT NOT NULL REFERENCES woo_product_raw(native_id),
  namespace TEXT NOT NULL CHECK(namespace IN ('category', 'tag', 'brand')),
  position INTEGER NOT NULL,
  term_id TEXT,
  name_raw TEXT,
  slug TEXT,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  PRIMARY KEY(native_id, namespace, position)
);

CREATE TABLE woo_product_image_raw (
  native_id TEXT NOT NULL REFERENCES woo_product_raw(native_id),
  position INTEGER NOT NULL,
  image_id TEXT,
  source_url TEXT,
  thumbnail_url TEXT,
  srcset_raw TEXT,
  sizes_raw TEXT,
  thumbnail_srcset_raw TEXT,
  thumbnail_sizes_raw TEXT,
  name_raw TEXT,
  alt_raw TEXT,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  PRIMARY KEY(native_id, position)
);

CREATE TABLE woo_product_json_value (
  native_id TEXT NOT NULL REFERENCES woo_product_raw(native_id),
  source_path TEXT NOT NULL,
  parent_path TEXT NOT NULL,
  key_name TEXT,
  ordinal INTEGER,
  value_type TEXT NOT NULL CHECK(value_type IN ('null', 'boolean', 'integer', 'number', 'string')),
  value_text TEXT,
  value_integer INTEGER,
  value_real REAL,
  value_boolean INTEGER CHECK(value_boolean IN (0, 1) OR value_boolean IS NULL),
  PRIMARY KEY(native_id, source_path)
);

CREATE TABLE _meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE _presets (
  name TEXT PRIMARY KEY,
  description TEXT,
  params TEXT DEFAULT '',
  sql TEXT
);

CREATE INDEX ix_woo_product_slug ON woo_product_raw(slug);
CREATE INDEX ix_woo_product_type ON woo_product_raw(product_type);
CREATE INDEX ix_woo_attribute_name ON woo_product_attribute_raw(name_raw);
CREATE INDEX ix_woo_attribute_taxonomy ON woo_product_attribute_raw(taxonomy);
CREATE INDEX ix_woo_attribute_term_name ON woo_product_attribute_term_raw(name_raw);
CREATE INDEX ix_woo_term_namespace_name ON woo_product_term_raw(namespace, name_raw);
CREATE INDEX ix_woo_json_value_key_text ON woo_product_json_value(key_name, value_text);
CREATE INDEX ix_woo_json_value_path ON woo_product_json_value(source_path);

CREATE VIEW catalogue_products AS
SELECT p.native_id AS product_id,
       p.slug,
       p.name_raw AS name,
       p.permalink,
       p.product_type,
       p.sku,
       p.stock_status,
       p.is_in_stock,
       p.is_purchasable,
       p.on_sale,
       price.currency_code,
       price.currency_minor_unit,
       price.price_raw,
       price.regular_price_raw,
       price.sale_price_raw,
       price.min_amount_raw,
       price.max_amount_raw,
       (SELECT image.source_url FROM woo_product_image_raw image
         WHERE image.native_id=p.native_id ORDER BY image.position LIMIT 1) AS primary_image_url,
       p.permalink AS product_url
FROM woo_product_raw p
LEFT JOIN woo_product_price_raw price ON price.native_id=p.native_id;

CREATE VIEW catalogue_attributes AS
SELECT attribute.native_id AS product_id,
       attribute.position AS attribute_position,
       attribute.attribute_id,
       attribute.name_raw AS attribute_name,
       attribute.taxonomy,
       attribute.has_variations,
       attribute.scalar_value_raw,
       term.position AS term_position,
       term.term_id,
       term.name_raw AS value,
       term.slug AS value_slug
FROM woo_product_attribute_raw attribute
LEFT JOIN woo_product_attribute_term_raw term
  ON term.native_id=attribute.native_id
 AND term.attribute_position=attribute.position;

CREATE VIEW catalogue_terms AS
SELECT native_id AS product_id, namespace, position, term_id, name_raw AS name, slug
FROM woo_product_term_raw;

CREATE VIEW catalogue_images AS
SELECT native_id AS product_id, position, image_id, source_url, thumbnail_url,
       alt_raw AS alt, name_raw AS name
FROM woo_product_image_raw;

CREATE VIEW catalogue_json_values AS
SELECT native_id AS product_id, source_path, parent_path, key_name, ordinal, value_type,
       value_text, value_integer, value_real, value_boolean
FROM woo_product_json_value;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _native_id(product: dict[str, Any]) -> str:
    value = product.get("id")
    if value is None or str(value).strip() == "":
        raise IntegrityError("Store API product has no native id")
    return str(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> int | None:
    return int(value) if isinstance(value, bool) else None


def _safe_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: headers[name]
        for name in HEADER_ALLOWLIST
        if name in headers
    }


def _require_positive_header(headers: dict[str, str], name: str) -> int:
    value = headers.get(name)
    try:
        parsed = int(value) if value is not None else 0
    except ValueError as exc:
        raise CaptureError(f"Store API response lacks a usable {name} header") from exc
    if parsed < 1:
        raise CaptureError(f"Store API response lacks a usable {name} header")
    return parsed


def _request_page(
    client: httpx.Client,
    endpoint: str,
    *,
    number: int,
    per_page: int,
    retries: int,
    retry_delay: float,
) -> StorePage:
    response: httpx.Response | None = None
    for attempt in range(retries + 1):
        response = client.get(endpoint, params={"per_page": per_page, "page": number})
        if response.status_code not in TRANSIENT_STATUSES or attempt == retries:
            break
        if retry_delay:
            time.sleep(retry_delay * (attempt + 1))
    assert response is not None
    if response.status_code != 200:
        raise CaptureError(
            f"Store API page {number} returned HTTP {response.status_code} after {retries + 1} attempt(s)"
        )
    body = response.content
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"Store API page {number} did not return JSON") from exc
    if not isinstance(payload, list) or not all(isinstance(product, dict) for product in payload):
        raise CaptureError(f"Store API page {number} did not return a product array")
    return StorePage(
        number=number,
        url=str(response.url),
        fetched_at=_now(),
        headers=_safe_headers(response.headers),
        body=body,
    )


def fetch_store_api(
    store_url: str,
    *,
    client: httpx.Client | None = None,
    per_page: int = 50,
    retries: int = 2,
    retry_delay: float = 0.25,
) -> StoreCapture:
    """Fetch one sealed public Store API population without transforming it.

    A valid capture requires both Woo denominator headers and an exact product
    count.  A missing header, duplicate native product id, failed page, or count
    mismatch fails closed rather than producing a plausible partial catalogue.
    """
    if not 1 <= per_page <= 100:
        raise ValueError("Woo Store API per_page must be between 1 and 100")
    site = store_url.rstrip("/")
    if not urlsplit(site).scheme or not urlsplit(site).netloc:
        raise ValueError("store_url must be an absolute URL")
    endpoint = site + PRODUCTS_PATH
    own_client = client is None
    if client is None:
        client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(90.0),
            headers={
                "Accept": "application/json",
                "User-Agent": "Flexchat WooCommerce preview capture/0.1 (+https://flexchat.dev)",
            },
        )
    try:
        first = _request_page(
            client, endpoint, number=1, per_page=per_page, retries=retries, retry_delay=retry_delay
        )
        expected_count = _require_positive_header(first.headers, "x-wp-total")
        expected_pages = _require_positive_header(first.headers, "x-wp-totalpages")
        computed_pages = ceil(expected_count / per_page)
        if expected_pages != computed_pages:
            raise CaptureError(
                "Store API denominator headers disagree: "
                f"x-wp-total={expected_count}, x-wp-totalpages={expected_pages}, per_page={per_page}"
            )

        pages = [first]
        for number in range(2, expected_pages + 1):
            pages.append(
                _request_page(
                    client, endpoint, number=number, per_page=per_page,
                    retries=retries, retry_delay=retry_delay,
                )
            )
        capture = StoreCapture(
            store_url=site,
            endpoint=endpoint,
            captured_at=first.fetched_at,
            per_page=per_page,
            expected_count=expected_count,
            expected_pages=expected_pages,
            pages=tuple(pages),
        )
        _validate_capture(capture)
        return capture
    finally:
        if own_client:
            client.close()


def _validate_capture(capture: StoreCapture) -> None:
    if len(capture.pages) != capture.expected_pages:
        raise IntegrityError("captured Store API page count does not match the sealed denominator")
    products = capture.products
    if len(products) != capture.expected_count:
        raise IntegrityError(
            f"captured {len(products)} products but Store API sealed denominator is {capture.expected_count}"
        )
    ids = [_native_id(product) for product in products]
    duplicates = sorted({native_id for native_id in ids if ids.count(native_id) > 1})
    if duplicates:
        raise IntegrityError(f"Store API capture has duplicate native product ids: {duplicates[:5]}")


def _product_document_id(capture_id: str, native_id: str) -> str:
    return f"{capture_id}:product:{native_id}"


def _page_document_id(capture_id: str, page: int) -> str:
    return f"{capture_id}:page:{page:04d}"


def _json_scalar_rows(
    value: Any,
    *,
    path: str = "$",
    parent_path: str = "$",
    key_name: str | None = None,
    ordinal: int | None = None,
) -> Iterator[tuple[str, str, str | None, int | None, str, str | None, int | None, float | None, int | None]]:
    """Expose every scalar in arbitrary Store API objects without inventing columns."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield from _json_scalar_rows(
                child, path=child_path, parent_path=path, key_name=str(key), ordinal=None
            )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from _json_scalar_rows(
                child, path=child_path, parent_path=path, key_name=None, ordinal=index
            )
        return
    if value is None:
        yield path, parent_path, key_name, ordinal, "null", None, None, None, None
    elif isinstance(value, bool):
        yield path, parent_path, key_name, ordinal, "boolean", None, None, None, int(value)
    elif isinstance(value, int):
        yield path, parent_path, key_name, ordinal, "integer", None, value, float(value), None
    elif isinstance(value, float):
        yield path, parent_path, key_name, ordinal, "number", None, None, value, None
    elif isinstance(value, str):
        yield path, parent_path, key_name, ordinal, "string", value, None, None, None
    else:  # JSON decoder will not normally produce this, but do not silently coerce a new type.
        raise IntegrityError(f"unsupported decoded JSON scalar type: {type(value).__name__}")


def _insert_product(
    db: sqlite3.Connection,
    capture: StoreCapture,
    product: dict[str, Any],
    *,
    source_page: int,
    source_position: int,
) -> None:
    native_id = _native_id(product)
    product_json = _canonical_json(product)
    product_bytes = product_json.encode("utf-8")
    product_document_id = _product_document_id(capture.capture_id, native_id)
    page_document_id = _page_document_id(capture.capture_id, source_page)
    db.execute(
        """INSERT INTO woo_native_document(
               document_id,capture_id,kind,ordinal,source_url,fetched_at,content_type,headers_json,body,body_sha256
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            product_document_id,
            capture.capture_id,
            "store_api_product",
            int(native_id) if native_id.isdigit() else source_position,
            str(product.get("permalink") or capture.endpoint),
            capture.captured_at,
            "application/json",
            None,
            product_bytes,
            _sha256_bytes(product_bytes),
        ),
    )
    db.execute(
        """INSERT INTO woo_product_raw(
               native_id,capture_id,product_document_id,page_document_id,source_page,source_position,
               slug,name_raw,permalink,product_type,sku,parent_id,short_description_raw,description_raw,
               price_html_raw,average_rating_raw,review_count,has_options,is_in_stock,is_on_backorder,
               is_password_protected,is_purchasable,on_sale,sold_individually,stock_status,raw_json,raw_sha256
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            native_id, capture.capture_id, product_document_id, page_document_id, source_page, source_position,
            _optional_text(product.get("slug")), _optional_text(product.get("name")),
            _optional_text(product.get("permalink")), _optional_text(product.get("type")),
            _optional_text(product.get("sku")), _optional_text(product.get("parent")),
            _optional_text(product.get("short_description")), _optional_text(product.get("description")),
            _optional_text(product.get("price_html")), _optional_text(product.get("average_rating")),
            _optional_int(product.get("review_count")), _optional_bool(product.get("has_options")),
            _optional_bool(product.get("is_in_stock")), _optional_bool(product.get("is_on_backorder")),
            _optional_bool(product.get("is_password_protected")), _optional_bool(product.get("is_purchasable")),
            _optional_bool(product.get("on_sale")), _optional_bool(product.get("sold_individually")),
            _optional_text((product.get("stock_availability") or {}).get("class"))
            if isinstance(product.get("stock_availability"), dict) else None,
            product_json, _sha256_text(product_json),
        ),
    )

    prices = product.get("prices")
    if isinstance(prices, dict):
        prices_json = _canonical_json(prices)
        price_range = prices.get("price_range") if isinstance(prices.get("price_range"), dict) else {}
        db.execute(
            """INSERT INTO woo_product_price_raw(
                   native_id,currency_code,currency_symbol,currency_minor_unit,price_raw,regular_price_raw,
                   sale_price_raw,min_amount_raw,max_amount_raw,price_json,price_sha256
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                native_id, _optional_text(prices.get("currency_code")), _optional_text(prices.get("currency_symbol")),
                _optional_int(prices.get("currency_minor_unit")), _optional_text(prices.get("price")),
                _optional_text(prices.get("regular_price")), _optional_text(prices.get("sale_price")),
                _optional_text(price_range.get("min_amount")), _optional_text(price_range.get("max_amount")),
                prices_json, _sha256_text(prices_json),
            ),
        )

    attributes = product.get("attributes") or []
    if not isinstance(attributes, list):
        raise IntegrityError(f"product {native_id} has non-list attributes")
    for attribute_position, attribute in enumerate(attributes):
        if not isinstance(attribute, dict):
            raise IntegrityError(f"product {native_id} has non-object attribute")
        attribute_json = _canonical_json(attribute)
        db.execute(
            """INSERT INTO woo_product_attribute_raw(
                   native_id,position,attribute_id,name_raw,taxonomy,has_variations,scalar_value_raw,raw_json,raw_sha256
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                native_id, attribute_position, _optional_text(attribute.get("id")),
                _optional_text(attribute.get("name")), _optional_text(attribute.get("taxonomy")),
                _optional_bool(attribute.get("has_variations")), _optional_text(attribute.get("value")),
                attribute_json, _sha256_text(attribute_json),
            ),
        )
        terms = attribute.get("terms") or []
        if not isinstance(terms, list):
            raise IntegrityError(f"product {native_id} attribute {attribute_position} has non-list terms")
        for term_position, term in enumerate(terms):
            if not isinstance(term, dict):
                raise IntegrityError(f"product {native_id} attribute {attribute_position} has non-object term")
            term_json = _canonical_json(term)
            db.execute(
                """INSERT INTO woo_product_attribute_term_raw(
                       native_id,attribute_position,position,term_id,name_raw,slug,raw_json,raw_sha256
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    native_id, attribute_position, term_position, _optional_text(term.get("id")),
                    _optional_text(term.get("name")), _optional_text(term.get("slug")),
                    term_json, _sha256_text(term_json),
                ),
            )

    for namespace, product_key in (("category", "categories"), ("tag", "tags"), ("brand", "brands")):
        terms = product.get(product_key) or []
        if not isinstance(terms, list):
            raise IntegrityError(f"product {native_id} has non-list {product_key}")
        for position, term in enumerate(terms):
            if not isinstance(term, dict):
                raise IntegrityError(f"product {native_id} has non-object {product_key} term")
            term_json = _canonical_json(term)
            db.execute(
                """INSERT INTO woo_product_term_raw(
                       native_id,namespace,position,term_id,name_raw,slug,raw_json,raw_sha256
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    native_id, namespace, position, _optional_text(term.get("id")),
                    _optional_text(term.get("name")), _optional_text(term.get("slug")),
                    term_json, _sha256_text(term_json),
                ),
            )

    images = product.get("images") or []
    if not isinstance(images, list):
        raise IntegrityError(f"product {native_id} has non-list images")
    for position, image in enumerate(images):
        if not isinstance(image, dict):
            raise IntegrityError(f"product {native_id} has non-object image")
        image_json = _canonical_json(image)
        db.execute(
            """INSERT INTO woo_product_image_raw(
                   native_id,position,image_id,source_url,thumbnail_url,srcset_raw,sizes_raw,
                   thumbnail_srcset_raw,thumbnail_sizes_raw,name_raw,alt_raw,raw_json,raw_sha256
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                native_id, position, _optional_text(image.get("id")), _optional_text(image.get("src")),
                _optional_text(image.get("thumbnail")), _optional_text(image.get("srcset")),
                _optional_text(image.get("sizes")), _optional_text(image.get("thumbnail_srcset")),
                _optional_text(image.get("thumbnail_sizes")), _optional_text(image.get("name")),
                _optional_text(image.get("alt")), image_json, _sha256_text(image_json),
            ),
        )

    db.executemany(
        """INSERT INTO woo_product_json_value(
               native_id,source_path,parent_path,key_name,ordinal,value_type,value_text,
               value_integer,value_real,value_boolean
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [
            (native_id, *row)
            for row in _json_scalar_rows(product)
        ],
    )


def build_staging_sqlite(capture: StoreCapture, output_path: str | Path) -> dict[str, Any]:
    """Build a sealed typed staging DB from an in-memory or reloaded raw capture."""
    _validate_capture(capture)
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"staging target already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(RAW_SCHEMA)
        manifest = capture.manifest
        db.execute(
            """INSERT INTO woo_capture(
                   capture_id,format,store_url,endpoint,captured_at,per_page,expected_count,expected_pages,
                   captured_count,manifest_sha256,product_ids_sha256
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                capture.capture_id, FORMAT, capture.store_url, capture.endpoint, capture.captured_at,
                capture.per_page, capture.expected_count, capture.expected_pages, len(capture.products),
                capture.manifest_sha256, manifest["product_ids_sha256"],
            ),
        )
        for page in capture.pages:
            db.execute(
                """INSERT INTO woo_native_document(
                       document_id,capture_id,kind,ordinal,source_url,fetched_at,content_type,headers_json,body,body_sha256
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    _page_document_id(capture.capture_id, page.number), capture.capture_id,
                    "store_api_page", page.number, page.url, page.fetched_at,
                    page.headers.get("content-type"), _canonical_json(page.headers), page.body, page.sha256,
                ),
            )
            for position, product in enumerate(page.products()):
                _insert_product(db, capture, product, source_page=page.number, source_position=position)
        db.commit()
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise IntegrityError(f"staging SQLite integrity check failed: {integrity}")
    except Exception:
        db.close()
        output.unlink(missing_ok=True)
        raise
    else:
        db.close()

    receipt = verify_staging_sqlite(output)
    receipt["staging_sha256"] = _file_sha256(output)
    return receipt


def _rows_count(db: sqlite3.Connection, table: str) -> int:
    return int(db.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0])


def verify_staging_sqlite(path: str | Path) -> dict[str, Any]:
    """Verify source-document and product-object identities from the DB itself."""
    target = Path(path).resolve()
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise IntegrityError("staging SQLite integrity check failed")
        captures = db.execute("SELECT * FROM woo_capture").fetchall()
        if len(captures) != 1:
            raise IntegrityError("staging database must contain exactly one capture")
        capture = captures[0]
        page_rows = db.execute(
            "SELECT * FROM woo_native_document WHERE kind='store_api_page' ORDER BY ordinal"
        ).fetchall()
        product_documents = db.execute(
            "SELECT * FROM woo_native_document WHERE kind='store_api_product' ORDER BY ordinal"
        ).fetchall()
        products = db.execute("SELECT * FROM woo_product_raw ORDER BY source_page,source_position").fetchall()
        if len(page_rows) != capture["expected_pages"]:
            raise IntegrityError("staging page-document count disagrees with capture denominator")
        if len(products) != capture["expected_count"]:
            raise IntegrityError("staging product count disagrees with capture denominator")
        if len(product_documents) != len(products):
            raise IntegrityError("staging product-document count disagrees with product rows")
        product_ids_from_pages: list[str] = []
        for row in page_rows:
            if _sha256_bytes(row["body"]) != row["body_sha256"]:
                raise IntegrityError(f"staging page document hash differs: {row['document_id']}")
            page_products = json.loads(bytes(row["body"]).decode("utf-8"))
            product_ids_from_pages.extend(_native_id(product) for product in page_products)
        product_ids = [row["native_id"] for row in products]
        if product_ids_from_pages != product_ids:
            raise IntegrityError("staging product identity/order differs from captured Store API pages")
        if _sha256_text(_canonical_json(product_ids)) != capture["product_ids_sha256"]:
            raise IntegrityError("staging product-id receipt differs")
        for row in products:
            if _sha256_text(row["raw_json"]) != row["raw_sha256"]:
                raise IntegrityError(f"staging product raw JSON hash differs: {row['native_id']}")
            parsed = json.loads(row["raw_json"])
            if _native_id(parsed) != row["native_id"]:
                raise IntegrityError(f"staging product raw JSON id differs: {row['native_id']}")
        return {
            "format": FORMAT,
            "capture_id": capture["capture_id"],
            "complete": True,
            "denominator_sealed": True,
            "expected_count": capture["expected_count"],
            "captured_count": len(products),
            "page_documents": len(page_rows),
            "product_documents": len(product_documents),
            "attributes": _rows_count(db, "woo_product_attribute_raw"),
            "attribute_terms": _rows_count(db, "woo_product_attribute_term_raw"),
            "taxonomy_terms": _rows_count(db, "woo_product_term_raw"),
            "images": _rows_count(db, "woo_product_image_raw"),
            "scalar_values": _rows_count(db, "woo_product_json_value"),
            "integrity_check": "ok",
        }


def verify_native_sqlite_in_connection(db: sqlite3.Connection) -> dict[str, Any]:
    """Run the non-file-dependent native Woo checks in an open database."""
    captures = db.execute("SELECT * FROM woo_capture").fetchall()
    if len(captures) != 1:
        raise IntegrityError("native Woo database must contain exactly one capture")
    capture = captures[0]
    products = db.execute("SELECT * FROM woo_product_raw ORDER BY source_page,source_position").fetchall()
    page_rows = db.execute(
        "SELECT * FROM woo_native_document WHERE kind='store_api_page' ORDER BY ordinal"
    ).fetchall()
    if len(products) != capture["expected_count"] or len(page_rows) != capture["expected_pages"]:
        raise IntegrityError("native Woo raw population differs from sealed capture")
    ids_from_pages: list[str] = []
    for row in page_rows:
        if _sha256_bytes(row["body"]) != row["body_sha256"]:
            raise IntegrityError("native Woo page hash changed")
        ids_from_pages.extend(_native_id(item) for item in json.loads(bytes(row["body"]).decode("utf-8")))
    ids = [row["native_id"] for row in products]
    if ids != ids_from_pages:
        raise IntegrityError("native Woo product rows no longer match source pages")
    return {
        "capture_id": capture["capture_id"],
        "captured_count": len(products),
        "expected_count": capture["expected_count"],
        "expected_pages": capture["expected_pages"],
    }


def resolve_cell_path(cell: str | Path) -> Path:
    """Resolve a Flex cell name or explicit SQLite path."""
    path = Path(cell)
    if path.suffix in {".db", ".sqlite", ".sqlite3"} or path.parent != Path("."):
        return path
    from flex.registry import CELLS_DIR

    CELLS_DIR.mkdir(parents=True, exist_ok=True)
    return CELLS_DIR / f"{path.name}.db"


def _module_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO _meta(key,value) VALUES(?,?)",
        (key, value),
    )


def install_stock_assets(db: sqlite3.Connection) -> None:
    """Install module presets; native catalogue views are created by RAW_SCHEMA."""
    root = _module_root()
    presets_dir = root / "stock" / "presets"
    if presets_dir.exists():
        from flex.retrieve.presets import install_presets

        install_presets(db, presets_dir, record_operation=False)


def finalize_native_cell(
    cell_path: str | Path,
    *,
    store_url: str,
    per_page: int,
    description: str | None,
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
) -> dict[str, Any]:
    """Attach query metadata/assets to an already verified native Woo database."""
    target = Path(cell_path)
    receipt = verify_staging_sqlite(target)
    with sqlite3.connect(target) as db:
        db.row_factory = sqlite3.Row
        install_stock_assets(db)
        _set_meta(db, "cell_type", "woocommerce")
        _set_meta(db, "description", description or "Raw WooCommerce Store API catalogue")
        _set_meta(db, "store_url", store_url.rstrip("/"))
        _set_meta(db, "per_page", str(per_page))
        _set_meta(db, "refresh_module", "flex.modules.woocommerce.compile.refresh")
        _set_meta(db, "refresh_interval", str(refresh_interval))
        _set_meta(db, "last_pull_at", _now())
        _set_meta(db, "scope", _canonical_json({
            "store_url": store_url.rstrip("/"),
            "endpoint": PRODUCTS_PATH,
            "per_page": per_page,
            "auth": "none",
            "mode": "public_store_api_raw",
        }))
        db.execute(
            "INSERT OR REPLACE INTO _presets(name,description,params,sql) VALUES(?,?,?,?)",
            (
                "self-orient",
                "WooCommerce native Store API orientation: capture, raw fields, query views, and integrity",
                "",
                SELF_ORIENT_SQL,
            ),
        )
        db.execute(
            "INSERT OR REPLACE INTO _presets(name,description,params,sql) VALUES(?,?,?,?)",
            (
                "orient",
                "WooCommerce native Store API orientation: capture, raw fields, query views, and integrity",
                "",
                ORIENT_SQL,
            ),
        )
        db.commit()
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise IntegrityError(f"native Woo SQLite integrity check failed after metadata: {integrity}")
        native = verify_native_sqlite_in_connection(db)
        catalogue_products = int(db.execute("SELECT COUNT(*) FROM catalogue_products").fetchone()[0])
        if catalogue_products != native["captured_count"]:
            raise IntegrityError("catalogue_products view differs from raw product population")
        for preset_name in ("self-orient", "orient"):
            if db.execute("SELECT 1 FROM _presets WHERE name=?", (preset_name,)).fetchone() is None:
                raise IntegrityError(f"required preset missing: {preset_name}")
        try:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.DatabaseError:
            pass
    receipt = verify_native_cell(target)
    receipt["cell_sha256"] = _file_sha256(target)
    return receipt


def verify_native_cell(path: str | Path) -> dict[str, Any]:
    """Verify a native Woo SQLite cell after metadata/presets are installed."""
    target = Path(path).resolve()
    staging = verify_staging_sqlite(target)
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        native = verify_native_sqlite_in_connection(db)
        catalogue_products = int(db.execute("SELECT COUNT(*) FROM catalogue_products").fetchone()[0])
        if catalogue_products != native["captured_count"]:
            raise IntegrityError("catalogue_products view differs from raw product population")
        presets = {
            row[0]
            for row in db.execute("SELECT name FROM _presets WHERE name IN ('self-orient','orient')").fetchall()
        }
        if presets != {"self-orient", "orient"}:
            raise IntegrityError("native Woo cell is missing required orientation presets")
        return {
            "format": FORMAT,
            "complete": True,
            "capture_id": native["capture_id"],
            "captured_count": native["captured_count"],
            "catalogue_products": catalogue_products,
            "page_documents": staging["page_documents"],
            "product_documents": staging["product_documents"],
            "attributes": staging["attributes"],
            "attribute_terms": staging["attribute_terms"],
            "taxonomy_terms": staging["taxonomy_terms"],
            "images": staging["images"],
            "scalar_values": staging["scalar_values"],
            "integrity_check": "ok",
        }


def build_cell_from_capture(
    capture: StoreCapture,
    cell_path: str | Path,
    *,
    description: str | None = None,
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
) -> dict[str, Any]:
    """Stage a full sealed snapshot and atomically replace the target cell.

    The existing target is untouched until capture, a transient same-schema
    SQLite file, metadata install, denominator checks, hash checks, preset
    checks, and SQLite integrity all pass. The replacement operation is a single
    filesystem rename.
    """
    target = Path(cell_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{target.stem}.woocommerce.", dir=target.parent))
    try:
        staging = staging_root / f"{target.name}.native"
        staging_receipt = build_staging_sqlite(capture, staging)
        source_sha256_before = _file_sha256(staging)
        final_receipt = finalize_native_cell(
            staging,
            store_url=capture.store_url,
            per_page=capture.per_page,
            description=description,
            refresh_interval=refresh_interval,
        )
        if _file_sha256(staging) == source_sha256_before:
            raise IntegrityError("native finalization did not add query metadata")
        # Verify again immediately before replace; this is the last gate.
        verify_native_cell(staging)
        os.replace(staging, target)
        return {
            "format": FORMAT,
            "cell": str(target),
            "atomic_replace": True,
            "staging_receipt": staging_receipt,
            "native_receipt": final_receipt,
        }
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def build_cell_from_store_api(
    store_url: str,
    cell_path: str | Path,
    *,
    description: str | None = None,
    per_page: int = 50,
    retries: int = 2,
    retry_delay: float = 0.25,
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
) -> dict[str, Any]:
    capture = fetch_store_api(
        store_url,
        per_page=per_page,
        retries=retries,
        retry_delay=retry_delay,
    )
    return build_cell_from_capture(
        capture,
        cell_path,
        description=description,
        refresh_interval=refresh_interval,
    )


def register_cell_path(
    cell_name: str,
    cell_path: str | Path,
    *,
    description: str | None = None,
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
) -> None:
    from flex.registry import register_cell

    register_cell(
        name=cell_name,
        path=str(cell_path),
        cell_type="woocommerce",
        description=description or "Raw WooCommerce Store API catalogue",
        lifecycle="refresh",
        refresh_interval=refresh_interval,
        refresh_module="flex.modules.woocommerce.compile.refresh",
        active=True,
        unlisted=False,
    )


def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    cell_path = resolve_cell_path(args.cell)
    cell_name = Path(args.cell).stem if str(args.cell).endswith((".db", ".sqlite", ".sqlite3")) else args.cell
    receipt = build_cell_from_store_api(
        args.url,
        cell_path,
        description=args.description,
        per_page=args.per_page,
        retries=args.retries,
        refresh_interval=args.refresh_interval,
    )
    register_cell_path(
        cell_name,
        cell_path,
        description=args.description,
        refresh_interval=args.refresh_interval,
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="public WooCommerce storefront URL")
    parser.add_argument("--cell", required=True, help="Flex cell name or explicit .db/.sqlite path")
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--refresh-interval", type=int, default=DEFAULT_REFRESH_INTERVAL)
    parser.add_argument("--description")
    args = parser.parse_args(argv)
    try:
        receipt = _run_cli(args)
    except (CaptureError, IntegrityError, OSError, ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
