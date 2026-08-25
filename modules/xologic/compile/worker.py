#!/usr/bin/env python3
"""Sealed XOLogic capture -> one source-faithful native SQLite cell."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any
from urllib.parse import urldefrag

FORMAT = "xologic-native-flex/v1"
BUILD_RECEIPT_SCHEMA = "xologic-native-flex-build/v3"
REFRESH_MODULE = "flex.modules.xologic.compile.refresh"
MODULE_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_EVIDENCE_SCHEMAS = frozenset({
    "xologic-lossless/v1", "xologic-lossless/v2",
})
REQUIRED_CAPTURE_ARTIFACTS = {
    "source_objects", "spec_occurrences", "relations", "asset_inventory",
}
INSTRUCTIONS_PATH = MODULE_ROOT / "stock" / "instructions.md"
TRANSFORMATION_PATHS = (
    "compile/worker.py",
    "stock/presets/orient.sql",
    "stock/sql/lighting_projection.sql",
    "stock/sql/product_search.sql",
)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE xologic_editions(
  edition_id TEXT PRIMARY KEY, capture_path TEXT NOT NULL, store_name TEXT,
  evidence_receipt TEXT NOT NULL, products_sha256 TEXT NOT NULL,
  product_count INTEGER NOT NULL, source_observation_count INTEGER NOT NULL,
  evidence_schema_version TEXT NOT NULL
);
CREATE TABLE xologic_source_observations(
  source_id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES xologic_editions,
  content_hash TEXT NOT NULL,
  source_namespace TEXT, object_kind TEXT, identity_key TEXT,
  requested_url TEXT NOT NULL, fetch_url TEXT, final_url TEXT, status INTEGER,
  parser_identity TEXT, config_identity TEXT, code_identity TEXT,
  descriptor_path TEXT NOT NULL
);
CREATE TABLE products(
  product_ref TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES xologic_editions,
  source_order INTEGER NOT NULL, url TEXT NOT NULL, handle TEXT,
  sku TEXT, partnumber TEXT, gtin TEXT, title TEXT NOT NULL, vendor TEXT,
  vendor_id TEXT, price TEXT, image_url TEXT, body_html TEXT,
  source_id TEXT, requested_url TEXT, product_key TEXT,
  product_json TEXT NOT NULL
);
CREATE TABLE product_source_observations(
  product_ref TEXT NOT NULL REFERENCES products, source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  PRIMARY KEY(product_ref,source_id)
);
CREATE TABLE source_properties(
  property_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT NOT NULL,
  source_order INTEGER NOT NULL, tag TEXT, property_attribute TEXT,
  property TEXT, value_attribute TEXT, value TEXT, locator TEXT
);
CREATE TABLE spec_occurrences(
  occurrence_id TEXT PRIMARY KEY, product_ref TEXT REFERENCES products,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT NOT NULL,
  source_namespace TEXT, object_kind TEXT, source_layer TEXT,
  section_heading TEXT, source_order INTEGER NOT NULL, surface_ordinal INTEGER,
  label TEXT NOT NULL, value TEXT NOT NULL, locator TEXT,
  raw_label TEXT, raw_value TEXT, row_classes TEXT,
  visible INTEGER, visibility_json TEXT NOT NULL
);
CREATE TABLE taxonomy_paths(
  product_ref TEXT NOT NULL REFERENCES products, depth INTEGER NOT NULL, label TEXT NOT NULL,
  PRIMARY KEY(product_ref,depth,label)
);
CREATE TABLE product_links(
  link_id TEXT PRIMARY KEY, product_ref TEXT REFERENCES products,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT, kind TEXT NOT NULL,
  url TEXT NOT NULL, locator TEXT, scope TEXT, raw_href TEXT,
  target_id TEXT, target_source_id TEXT, target_namespace TEXT,
  target_object_kind TEXT, source_namespace TEXT, source_object_kind TEXT,
  surface_ordinal INTEGER, tag TEXT, visible_text TEXT, section_heading TEXT,
  fragment_meaningful INTEGER
);
CREATE TABLE product_resources(
  resource_id TEXT PRIMARY KEY, product_ref TEXT REFERENCES products,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT, kind TEXT NOT NULL,
  url TEXT NOT NULL, asset_id TEXT, raw_href TEXT, locator TEXT,
  locators_json TEXT NOT NULL, scope TEXT, surface_ordinal INTEGER,
  tag TEXT, visible_text TEXT, section_heading TEXT
);
CREATE INDEX ix_source_requested ON xologic_source_observations(requested_url);
CREATE INDEX ix_products_identity ON products(product_key,source_id,requested_url);
CREATE INDEX ix_spec_lookup ON spec_occurrences(label,value,product_ref);
CREATE INDEX ix_spec_product ON spec_occurrences(product_ref,label,source_order);
CREATE INDEX ix_taxonomy_lookup ON taxonomy_paths(label,depth,product_ref);
CREATE INDEX ix_links_product ON product_links(product_ref,kind);
CREATE INDEX ix_resources_product ON product_resources(product_ref,kind);

CREATE VIEW spec_occurrence_preference AS
SELECT o.*,
       CASE lower(COALESCE(o.source_layer,''))
         WHEN 'additionaldata' THEN 0 WHEN 'additional data' THEN 0
         WHEN 'standarddata' THEN 1 WHEN 'standard data' THEN 1 ELSE 2 END AS layer_rank,
       row_number() OVER (
         PARTITION BY o.product_ref,lower(o.label)
         ORDER BY CASE lower(COALESCE(o.source_layer,''))
                    WHEN 'additionaldata' THEN 0 WHEN 'additional data' THEN 0
                    WHEN 'standarddata' THEN 1 WHEN 'standard data' THEN 1 ELSE 2 END,
                  COALESCE(o.visible,0) DESC,o.source_order,o.occurrence_id
       ) AS preference_rank
FROM spec_occurrences o WHERE o.product_ref IS NOT NULL;

CREATE VIEW preferred_spec_occurrences AS
SELECT * FROM spec_occurrence_preference WHERE preference_rank=1;

CREATE VIEW spec_conflicts AS
SELECT product_ref,label,count(DISTINCT value) AS distinct_values,
       group_concat(DISTINCT value) AS values_seen
FROM spec_occurrences WHERE product_ref IS NOT NULL
GROUP BY product_ref,lower(label) HAVING count(DISTINCT value)>1;

CREATE VIEW resolved_products AS
SELECT p.*,
       (SELECT label FROM taxonomy_paths t WHERE t.product_ref=p.product_ref AND depth=1 ORDER BY label LIMIT 1) AS category,
       (SELECT label FROM taxonomy_paths t WHERE t.product_ref=p.product_ref AND depth=2 ORDER BY label LIMIT 1) AS subcategory,
       COALESCE((SELECT json_group_object(label,value) FROM preferred_spec_occurrences s
                 WHERE s.product_ref=p.product_ref),'{}') AS preferred_specs_json
FROM products p;

CREATE VIEW product_specification AS
SELECT occurrence_id,product_ref,source_id,source_order,surface_ordinal,
       label,value,raw_label,raw_value,locator,row_classes,visible,visibility_json
FROM spec_occurrences
WHERE section_heading='Product Specification'
   OR source_layer='product_specification';

CREATE VIEW additional_product_details AS
SELECT occurrence_id,product_ref,source_id,source_layer,source_order,surface_ordinal,
       label,value,raw_label,raw_value,locator,row_classes,visible,visibility_json
FROM spec_occurrences
WHERE section_heading='Additional product details'
   OR source_layer IN ('standardData','additionalData');

CREATE VIEW other_details AS
SELECT occurrence_id,product_ref,source_id,source_order,surface_ordinal,
       label,value,raw_label,raw_value,locator,row_classes,visible,visibility_json
FROM spec_occurrences
WHERE section_heading='Other Details' OR source_layer='other_details';

CREATE VIEW images AS
SELECT link_id AS relation_id,product_ref,source_id,surface_ordinal,
       visible_text,url,raw_href,locator,tag,section_heading,scope
FROM product_links WHERE kind='image_for';

CREATE VIEW downloads AS
SELECT link_id AS relation_id,product_ref,source_id,surface_ordinal,
       visible_text,url,raw_href,locator,tag,section_heading,scope
FROM product_links WHERE kind='document_for';

CREATE VIEW options AS
SELECT link_id AS relation_id,product_ref,source_id,surface_ordinal,
       visible_text,url,raw_href,locator,tag,section_heading,
       fragment_meaningful,target_id,target_source_id
FROM product_links WHERE kind='option_for';

CREATE VIEW additional_options_that_would_change_other_selections AS
SELECT * FROM options
WHERE section_heading='Additional options that would change other selections';

CREATE VIEW collection_products AS
SELECT link_id AS relation_id,product_ref,source_id,surface_ordinal,
       visible_text,url,raw_href,locator,tag,target_id,target_source_id
FROM product_links
WHERE kind='product_link' AND section_heading='More In This Collection';

CREATE VIEW related_products AS
SELECT link_id AS relation_id,product_ref,source_id,surface_ordinal,
       visible_text,url,raw_href,locator,tag,target_id,target_source_id
FROM product_links
WHERE kind='product_link' AND section_heading='Related Products';

CREATE VIEW agent_product_status AS
SELECT
  p.product_ref,
  CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM product_source_observations AS pso
      WHERE pso.product_ref=p.product_ref
    ) THEN 'absent'
    WHEN EXISTS (
      SELECT 1
      FROM product_source_observations AS pso
      JOIN xologic_source_observations AS so USING(source_id)
      WHERE pso.product_ref=p.product_ref AND so.status BETWEEN 200 AND 299
    ) THEN 'live'
    ELSE 'stale'
  END AS details_status,
  (
    SELECT count(*)
    FROM product_source_observations AS pso
    JOIN xologic_source_observations AS so USING(source_id)
    WHERE pso.product_ref=p.product_ref
  ) AS terminal_observation_count,
  (
    SELECT count(DISTINCT COALESCE(so.source_namespace,''))
    FROM product_source_observations AS pso
    JOIN xologic_source_observations AS so USING(source_id)
    WHERE pso.product_ref=p.product_ref
  ) AS terminal_provider_count,
  COALESCE((
    SELECT json_group_array(json_object(
      'source_namespace',q.source_namespace,
      'object_kind',q.object_kind,
      'parser_identity',q.parser_identity,
      'config_identity',q.config_identity,
      'code_identity',q.code_identity,
      'observation_count',q.observation_count
    ))
    FROM (
      SELECT
        so.source_namespace,so.object_kind,so.parser_identity,
        so.config_identity,so.code_identity,count(*) AS observation_count
      FROM product_source_observations AS pso
      JOIN xologic_source_observations AS so USING(source_id)
      WHERE pso.product_ref=p.product_ref
      GROUP BY so.source_namespace,so.object_kind,so.parser_identity,
               so.config_identity,so.code_identity
      ORDER BY so.source_namespace,so.object_kind,so.parser_identity,
               so.config_identity,so.code_identity
    ) AS q
  ),'[]') AS terminal_providers,
  (
    SELECT count(DISTINCT q.source_id)
    FROM (
      SELECT l.source_id
      FROM product_links AS l
      WHERE l.product_ref=p.product_ref AND l.scope='discover_only'
      UNION ALL
      SELECT x.source_id
      FROM product_resources AS x
      WHERE x.product_ref=p.product_ref AND x.scope='discover_only'
    ) AS q
  ) AS discovery_observation_count,
  (
    SELECT count(*)
    FROM json_each(
      CASE WHEN json_valid(p.product_json) THEN p.product_json ELSE '{}' END,
      '$.variants'
    ) AS v
    WHERE trim(COALESCE(CAST(json_extract(v.value,'$.price') AS TEXT),''))<>''
  ) AS listing_price_observation_count,
  (
    SELECT count(DISTINCT trim(CAST(json_extract(v.value,'$.price') AS TEXT)))
    FROM json_each(
      CASE WHEN json_valid(p.product_json) THEN p.product_json ELSE '{}' END,
      '$.variants'
    ) AS v
    WHERE trim(COALESCE(CAST(json_extract(v.value,'$.price') AS TEXT),''))<>''
  ) AS listing_price_distinct_count,
  CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM products AS px
      WHERE NOT EXISTS (
        SELECT 1 FROM product_source_observations AS pso
        WHERE pso.product_ref=px.product_ref
      )
    ) THEN 'true'
    ELSE 'false'
  END AS terminal_coverage_complete,
  CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM products AS px
      WHERE NOT EXISTS (
        SELECT 1
        FROM product_source_observations AS pso
        JOIN xologic_source_observations AS so USING(source_id)
        WHERE pso.product_ref=px.product_ref AND so.status BETWEEN 200 AND 299
      )
    ) THEN 'true'
    ELSE 'false'
  END AS live_pdp_details_complete,
  CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM products AS px
      WHERE NOT EXISTS (
        SELECT 1
        FROM product_source_observations AS pso
        JOIN xologic_source_observations AS so USING(source_id)
        WHERE pso.product_ref=px.product_ref AND so.status BETWEEN 200 AND 299
      )
    ) THEN 'true'
    ELSE 'false'
  END AS pdp_details_complete,
  'module-native-derived-agent-dossier' AS dossier_scope
FROM products AS p;
"""


class CaptureError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sha256_uri(digest: str) -> str:
    return digest if digest.startswith("sha256:") else "sha256:" + digest


def _transformation_hash() -> str:
    file_hashes = {}
    for relative in TRANSFORMATION_PATHS:
        path = MODULE_ROOT / relative
        if not path.is_file():
            raise CaptureError(f"missing XOLogic module source: {path}")
        file_hashes[relative] = _sha256_uri(_digest_file(path))
    return _sha256_uri(
        _digest_bytes(_canonical(file_hashes).encode("utf-8"))
    )


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(_canonical(value) + "\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _stable_id(prefix: str, *values: Any) -> str:
    return prefix + "_" + _digest_bytes(_canonical(values).encode())


def _load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise CaptureError(f"{path} must contain a rows array")
    return rows


def _capture_paths(capture: Path) -> dict[str, Path]:
    root = capture.resolve()
    paths = {
        "root": root,
        "products": root / "structured" / "products.json",
        "receipt": root / "evidence" / "receipt.json",
        "source_objects": root / "evidence" / "source_objects.json",
        "spec_occurrences": root / "evidence" / "spec_occurrences.json",
        "relations": root / "evidence" / "relations.json",
        "asset_inventory": root / "evidence" / "asset_inventory.json",
    }
    missing = [str(path) for key,path in paths.items() if key != "root" and not path.is_file()]
    if missing:
        raise CaptureError("capture is missing: " + ", ".join(missing))
    return paths


def _verify_receipt(paths: dict[str, Path]) -> dict[str, Any]:
    receipt = json.loads(paths["receipt"].read_text())
    if (receipt.get("schema_version") not in SUPPORTED_EVIDENCE_SCHEMAS
            or receipt.get("complete") is not True
            or receipt.get("sealed") is not True):
        raise CaptureError("XOLogic evidence receipt is not sealed and complete")
    if receipt.get("frontier_exhausted") is not True:
        raise CaptureError("XOLogic evidence frontier is not exhausted")
    if not isinstance(receipt.get("receipt_hash"), str):
        raise CaptureError("XOLogic evidence receipt has no identity")
    artifact_hashes = receipt.get("artifact_hashes")
    if (not isinstance(artifact_hashes, dict)
            or not REQUIRED_CAPTURE_ARTIFACTS.issubset(artifact_hashes)):
        raise CaptureError("XOLogic evidence receipt lacks required artifact hashes")
    for artifact in sorted(REQUIRED_CAPTURE_ARTIFACTS):
        expected = artifact_hashes[artifact]
        path = paths["root"] / "evidence" / f"{artifact}.json"
        if not path.is_file() or "sha256:" + _digest_file(path) != expected:
            raise CaptureError(f"evidence artifact hash mismatch: {artifact}")
    return receipt


def _unique_evidence_ids(rows: list[dict[str, Any]], field: str,
                         artifact: str) -> None:
    values = [row.get(field) for row in rows]
    if any(not isinstance(value, str) or not value for value in values):
        raise CaptureError(f"{artifact} lacks exact {field} values")
    if len(values) != len(set(values)):
        raise CaptureError(f"{artifact} contains duplicate {field} values")


def _product_ref(product: dict[str, Any]) -> str:
    identity = product.get("source_id") or product.get("requested_url") or product.get("url")
    if not isinstance(identity,str) or not identity:
        raise CaptureError("product has no URL")
    return _stable_id("product", identity)


def _price(product: dict[str, Any]) -> str | None:
    if product.get("price") is not None:
        return str(product["price"])
    variants = product.get("variants") or []
    if (len(variants) == 1 and isinstance(variants[0],dict)
            and variants[0].get("price") is not None):
        return str(variants[0]["price"])
    return None


def _url_index(products: list[dict[str, Any]]) -> tuple[dict[str,str],dict[str,str]]:
    exact: dict[str,str] = {}
    base: dict[str,str] = {}
    exact_ambiguous: set[str] = set()
    base_ambiguous: set[str] = set()
    for product in products:
        ref = _product_ref(product)
        aliases = {
            str(value) for value in (product.get("url"), product.get("requested_url"))
            if isinstance(value, str) and value
        }
        aliases.update(
            str(alias.get(field))
            for alias in (product.get("source_aliases") or [])
            if isinstance(alias, dict)
            for field in ("requested_url", "final_url")
            if isinstance(alias.get(field), str) and alias.get(field)
        )
        for url in aliases:
            if url in exact and exact[url] != ref:
                exact_ambiguous.add(url)
            else:
                exact[url] = ref
            key = urldefrag(url)[0]
            if key in base and base[key] != ref:
                base_ambiguous.add(key)
            else:
                base[key] = ref
    for key in exact_ambiguous:
        exact.pop(key,None)
    for key in base_ambiguous:
        base.pop(key,None)
    return exact,base


def _map_source(row: dict[str,Any], exact: dict[str,str], base: dict[str,str],
                source_refs: dict[str,str] | None = None) -> str | None:
    source_id = row.get("source_id")
    if source_refs and isinstance(source_id, str) and source_id in source_refs:
        return source_refs[source_id]
    for field in ("requested_url","fetch_url","final_url"):
        value = row.get(field)
        if isinstance(value,str) and value in exact:
            return exact[value]
    for field in ("requested_url","fetch_url","final_url"):
        value = row.get(field)
        if isinstance(value,str) and urldefrag(value)[0] in base:
            return base[urldefrag(value)[0]]
    return None


def inspect_capture(capture: Path) -> dict[str, Any]:
    paths = _capture_paths(capture)
    receipt = _verify_receipt(paths)
    products = json.loads(paths["products"].read_text()).get("products")
    if not isinstance(products,list):
        raise CaptureError("products.json has no products array")
    product_urls = {p.get("url") for p in products if isinstance(p,dict) and p.get("url")}
    source_products = set(product_urls)
    for row in _load_rows(paths["source_objects"]):
        url = row.get("final_url")
        status = row.get("status")
        has_name = any(
            isinstance(prop,dict)
            and str(prop.get("property") or "").lower() == "schema:name"
            and prop.get("value") is not None
            for prop in (row.get("structured_properties") or [])
        )
        if (isinstance(url,str) and "/sku-" in url and isinstance(status,int)
                and 200 <= status < 300 and has_name):
            source_products.add(url)
    return {"capture_path":str(paths["root"]),"seed_products":len(products),
            "products":len(source_products),
            "source_observations":receipt.get("artifact_counts",{}).get("source_objects")}


def _build_database(capture: Path, output: Path, store_name: str | None) -> None:
    paths = _capture_paths(capture)
    receipt = _verify_receipt(paths)
    evidence_schema = str(receipt["schema_version"])
    products_payload = json.loads(paths["products"].read_text())
    products = products_payload.get("products")
    if not isinstance(products,list) or any(not isinstance(p,dict) for p in products):
        raise CaptureError("products.json has no object products array")
    source_rows = _load_rows(paths["source_objects"])
    occurrence_rows = _load_rows(paths["spec_occurrences"])
    relation_rows = _load_rows(paths["relations"])
    asset_rows = _load_rows(paths["asset_inventory"])
    artifact_rows = {
        "source_objects": source_rows,
        "spec_occurrences": occurrence_rows,
        "relations": relation_rows,
        "asset_inventory": asset_rows,
    }
    for artifact, rows in artifact_rows.items():
        expected = (receipt.get("artifact_counts") or {}).get(artifact)
        if expected is None or len(rows) != int(expected):
            raise CaptureError(f"evidence artifact count mismatch: {artifact}")
    _unique_evidence_ids(source_rows, "source_id", "source_objects")
    _unique_evidence_ids(asset_rows, "asset_id", "asset_inventory")
    if evidence_schema == "xologic-lossless/v2":
        _unique_evidence_ids(occurrence_rows, "occurrence_id", "spec_occurrences")
        _unique_evidence_ids(relation_rows, "relation_id", "relations")
    source_ids = {str(row.get("source_id")) for row in source_rows if row.get("source_id")}
    expected_counts = {
        "source_properties": sum(len(row.get("structured_properties") or []) for row in source_rows),
        "spec_occurrences": len(occurrence_rows),
        "product_links": sum(
            1 for row in relation_rows
            if str(row.get("source_id") or "") in source_ids
            and isinstance(row.get("target_url"), str) and row.get("target_url")
        ),
        "product_resources": sum(
            sum(1 for source_id in (row.get("source_ids") or []) if str(source_id) in source_ids)
            for row in asset_rows
            if isinstance(row.get("url"), str) and row.get("url")
        ),
    }
    products_sha = _digest_file(paths["products"])
    receipt_id = str(receipt.get("receipt_hash") or "sha256:" + _digest_file(paths["receipt"]))
    edition_id = _stable_id("edition",products_sha,receipt_id)
    exact_urls,base_urls = _url_index(products)
    source_refs = {
        str(product["source_id"]): _product_ref(product)
        for product in products
        if isinstance(product.get("source_id"), str) and product.get("source_id")
    }
    for product in products:
        ref = _product_ref(product)
        for alias in product.get("source_aliases") or []:
            if isinstance(alias, dict) and isinstance(alias.get("source_id"), str):
                prior = source_refs.setdefault(str(alias["source_id"]), ref)
                if prior != ref:
                    raise CaptureError("one source identity maps to multiple product rows")
    provider_keys_by_source_id = {
        str(row["source_id"]): row.get("provider_keys") or {}
        for row in source_rows
    }

    db = sqlite3.connect(output)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        from flex.envelope import install_cell_core
        install_cell_core(db)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO xologic_editions VALUES(?,?,?,?,?,?,?,?)",
                   (edition_id,str(paths["root"]),store_name,receipt_id,products_sha,
                    len(products),len(source_rows),evidence_schema))
        metadata = {
            "cell_type":"xologic","provider":"xologic",
            "profile":"commerce-native","schema_version":FORMAT,
            "description":f"{store_name or 'XOLogic'} source-faithful native catalogue",
            "capture_path":str(paths["root"]),"store_name":store_name or "XOLogic",
            "evidence_receipt":receipt_id,"products_sha256":products_sha,
            "evidence_schema_version": evidence_schema,
            **{f"expected_{key}": str(value) for key,value in expected_counts.items()},
            "lifecycle":"refresh","refresh_module":REFRESH_MODULE,
            "authority":"provider-native-capture",
            "capabilities":_canonical({
                "native_products":"present",
                "source_recovery":"present",
                "taxonomy":"present",
                "product_links":"present",
                "product_resources":"present",
                "text_retrieval":"products_fts",
                "text_embeddings":"absent",
                "image_embeddings":"absent",
                "relationship_interop":"absent",
                "tree_navigation":"absent",
                "operations_log":"absent",
            }),
        }
        db.executemany("INSERT INTO _metadata(key,value) VALUES(?,?)",metadata.items())
        from flex.retrieve.presets import install_presets
        install_presets(db, Path(__file__).resolve().parent.parent / "stock" / "presets",
                        record_operation=False)

        for order,product in enumerate(products,1):
            ref = _product_ref(product)
            url = product.get("url")
            title = product.get("title")
            if not isinstance(url,str) or not isinstance(title,str):
                raise CaptureError(f"product {order} lacks URL/title")
            db.execute("""INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ref,edition_id,order,url,product.get("handle"),product.get("sku"),
                 product.get("partnumber"),product.get("gtin"),title,product.get("vendor"),
                 None if product.get("vendor_id") is None else str(product.get("vendor_id")),
                 _price(product),product.get("image"),product.get("body_html"),
                 product.get("source_id"),product.get("requested_url"),
                 product.get("product_key"),_canonical(product)))
            for depth,label in enumerate(product.get("tags") or [],1):
                db.execute("INSERT OR IGNORE INTO taxonomy_paths VALUES(?,?,?)",(ref,depth,str(label)))

        source_hash: dict[str,str] = {}
        source_product: dict[str,str|None] = {}
        verified_bodies: set[str] = set()
        root = paths["root"]
        for row in source_rows:
            source_id = row.get("source_id")
            descriptor = row.get("raw_descriptor")
            requested = row.get("requested_url")
            if not isinstance(source_id,str) or not isinstance(requested,str) or not isinstance(descriptor,dict):
                raise CaptureError("source observation lacks identity/URL/raw descriptor")
            relative,expected = descriptor.get("path"),str(descriptor.get("sha256") or "").removeprefix("sha256:")
            if not isinstance(relative,str) or not expected:
                raise CaptureError(f"source {source_id} has invalid raw descriptor")
            raw_path = (root / relative).resolve()
            if not raw_path.is_relative_to(root) or not raw_path.is_file():
                raise CaptureError(f"source body escapes capture or is missing: {relative}")
            if expected not in verified_bodies:
                body = raw_path.read_bytes()
                if _digest_bytes(body) != expected or (descriptor.get("bytes") is not None and len(body)!=int(descriptor["bytes"])):
                    raise CaptureError(f"source body hash/size mismatch: {relative}")
                verified_bodies.add(expected)
            source_hash[source_id]=expected
            mapped=_map_source(row,exact_urls,base_urls,source_refs)
            source_product[source_id]=mapped
            db.execute("""INSERT INTO xologic_source_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id,edition_id,expected,row.get("source_namespace"),row.get("object_kind"),
                 row.get("identity_key"),requested,row.get("fetch_url"),row.get("final_url"),row.get("status"),
                 row.get("parser_identity"),row.get("config_identity"),row.get("code_identity"),relative))
            if mapped:
                db.execute("INSERT OR IGNORE INTO product_source_observations VALUES(?,?)",(mapped,source_id))
            for prop in row.get("structured_properties") or []:
                if not isinstance(prop,dict): continue
                values=(source_id,expected,prop.get("surface_ordinal"),prop.get("tag"),prop.get("property_attribute"),
                        prop.get("property"),prop.get("value_attribute"),prop.get("value"),prop.get("locator"))
                pid=_stable_id("property",*values)
                db.execute("INSERT OR IGNORE INTO source_properties VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (pid,source_id,expected,int(prop.get("surface_ordinal") or 0),prop.get("tag"),prop.get("property_attribute"),
                     prop.get("property"),prop.get("value_attribute"),prop.get("value"),prop.get("locator")))

        # products.json is an older compatibility frontier. XOLogic option links
        # can expose additional live SKU PDPs, so promote every source-backed
        # RDFa product before mapping occurrences. The source body remains the
        # authority; this is only a rebuildable relational projection.
        additions = db.execute("""
            WITH ranked AS (
              SELECT s.*,row_number() OVER (
                PARTITION BY s.final_url
                ORDER BY CASE WHEN s.requested_url=s.fetch_url THEN 0 ELSE 1 END,
                         s.requested_url,s.source_id) source_rank
              FROM xologic_source_observations s
              WHERE s.status BETWEEN 200 AND 299 AND s.final_url LIKE '%/sku-%'
            ), props AS (
              SELECT s.source_id,
                max(CASE WHEN lower(p.property)='schema:name' THEN p.value END) name,
                max(CASE WHEN lower(p.property)='schema:sku' THEN p.value END) sku,
                max(CASE WHEN lower(p.property)='schema:mpn' THEN p.value END) mpn,
                max(CASE WHEN lower(p.property) IN ('schema:gtin','schema:gtin12','schema:gtin13') THEN p.value END) gtin,
                max(CASE WHEN lower(p.property)='schema:price' THEN p.value END) price,
                max(CASE WHEN lower(p.property)='schema:image' THEN p.value END) image,
                max(CASE WHEN lower(p.property)='schema:description' THEN p.value END) description
              FROM xologic_source_observations s
              JOIN source_properties p
                ON p.source_id=s.source_id AND p.content_hash=s.content_hash
              GROUP BY s.source_id
            )
            SELECT r.source_id,r.requested_url,r.final_url,
                   p.name,p.sku,p.mpn,p.gtin,p.price,p.image,p.description
            FROM ranked r JOIN props p USING(source_id)
            WHERE r.source_rank=1 AND p.name IS NOT NULL
              AND NOT EXISTS(SELECT 1 FROM products x WHERE x.url=r.final_url)
            ORDER BY r.final_url
        """).fetchall()
        next_order = int(db.execute("SELECT COALESCE(max(source_order),0) FROM products").fetchone()[0])
        for offset,(source_id,requested_url,url,name,sku,mpn,gtin,
                   price,image,description) in enumerate(additions,1):
            provider_keys = provider_keys_by_source_id.get(str(source_id), {})
            projected = {"url":url,"title":name,"sku":sku,"partnumber":mpn or sku,
                         "gtin":gtin,"variants":[{"price":price}] if price is not None else [],
                         "image":image,"body_html":description,"source_projection":"xologic-rdfa",
                         "source_id":source_id,"requested_url":requested_url,
                         "product_key":provider_keys.get("product_key")}
            db.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_stable_id("product",source_id),edition_id,next_order+offset,url,url,sku,mpn or sku,gtin,
                 name,provider_keys.get("vendor"),None,price,image,description,source_id,
                 requested_url,provider_keys.get("product_key"),_canonical(projected)))

        product_rows = [json.loads(row[0]) for row in db.execute(
            "SELECT product_json FROM products ORDER BY source_order,product_ref"
        )]
        exact_urls,base_urls = _url_index(product_rows)
        source_refs = {
            str(source_id): str(product_ref)
            for source_id,product_ref in db.execute(
                "SELECT source_id,product_ref FROM products WHERE source_id IS NOT NULL"
            )
        }
        source_product={
            str(row["source_id"]):_map_source(row,exact_urls,base_urls,source_refs)
            for row in source_rows
        }
        for source_id,product_ref in source_product.items():
            if product_ref:
                db.execute("INSERT OR IGNORE INTO product_source_observations VALUES(?,?)",(product_ref,source_id))
        db.execute("UPDATE xologic_editions SET product_count=(SELECT count(*) FROM products) WHERE edition_id=?",
                   (edition_id,))

        for row in occurrence_rows:
            sid=row.get("source_id"); content=source_hash.get(str(sid))
            if not content: raise CaptureError(f"occurrence references unknown source: {sid}")
            label=row.get("clean_label") if row.get("clean_label") is not None else row.get("label")
            value=row.get("clean_value") if row.get("clean_value") is not None else row.get("value")
            if label is None or value is None: raise CaptureError("occurrence lacks label/value")
            signature=(str(sid),source_product.get(str(sid)),content,row.get("source_layer"),row.get("source_order"),
                       row.get("surface_ordinal"),str(label),str(value),row.get("locator"),row.get("raw_label"),row.get("raw_value"))
            oid=row.get("occurrence_id") or _stable_id("occurrence",*signature)
            visible=(row.get("visibility_evidence") or {}).get("hidden") is False
            db.execute("INSERT INTO spec_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid,source_product.get(str(sid)),str(sid),content,
                 row.get("source_namespace"),row.get("object_kind"),row.get("source_layer"),
                 row.get("section_heading"),int(row.get("source_order") or 0),
                 row.get("surface_ordinal"),str(label),str(value),row.get("locator"),
                 row.get("raw_label"),row.get("raw_value"),row.get("row_classes"),
                 int(visible),_canonical(row.get("visibility_evidence") or {})))

        for row in relation_rows:
            sid=str(row.get("source_id") or ""); content=source_hash.get(sid); url=row.get("target_url")
            if not content or not isinstance(url,str) or not url: continue
            product_ref=source_product.get(sid)
            signature=(sid,product_ref,content,row.get("kind"),url,row.get("locator"),row.get("scope"),row.get("target_id"))
            db.execute("INSERT INTO product_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row.get("relation_id") or _stable_id("link",*signature),product_ref,sid,content,
                 str(row.get("kind") or "link"),url,row.get("locator"),row.get("scope"),
                 row.get("raw_href"),row.get("target_id"),row.get("target_source_id"),
                 row.get("target_namespace"),row.get("target_object_kind"),
                 row.get("source_namespace"),row.get("source_object_kind"),
                 row.get("surface_ordinal"),row.get("tag"),
                 row.get("visible_text"),row.get("section_heading"),
                 int(bool(row.get("fragment_meaningful")))))

        for row in asset_rows:
            url=row.get("url")
            if not isinstance(url,str) or not url: continue
            source_ids=[str(v) for v in (row.get("source_ids") or [])] or [""]
            mapped=[(sid,source_product.get(sid),source_hash.get(sid)) for sid in source_ids
                    if source_hash.get(sid)]
            for sid,product_ref,content in sorted(mapped,key=lambda item:(item[0],item[1] or "",item[2] or "")):
                signature=(sid,product_ref,content,row.get("object_kind"),url,row.get("asset_id"))
                locators=[str(locator) for locator in (row.get("locators") or [])]
                db.execute("INSERT OR IGNORE INTO product_resources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_stable_id("resource",*signature),product_ref,sid,content,str(row.get("object_kind") or "resource"),url,
                     row.get("asset_id"),row.get("raw_href"),locators[0] if locators else None,
                     _canonical(locators),row.get("scope"),row.get("surface_ordinal"),row.get("tag"),
                     row.get("visible_text"),row.get("section_heading")))

        # Source-only option products receive their published taxonomy through
        # the same native occurrence surface rather than Python label folding.
        db.execute("""INSERT OR IGNORE INTO taxonomy_paths(product_ref,depth,label)
                      SELECT product_ref,1,value FROM preferred_spec_occurrences
                      WHERE lower(label)='category'""")
        db.execute("""INSERT OR IGNORE INTO taxonomy_paths(product_ref,depth,label)
                      SELECT product_ref,2,value FROM preferred_spec_occurrences
                      WHERE lower(label)='subcategory'""")
        projection_sql = (Path(__file__).resolve().parent.parent / "stock" / "sql" /
                          "lighting_projection.sql")
        if not projection_sql.is_file():
            raise CaptureError(f"missing XOLogic SQL projection: {projection_sql}")
        db.executescript(projection_sql.read_text())
        search_sql = (Path(__file__).resolve().parent.parent / "stock" / "sql" /
                      "product_search.sql")
        if not search_sql.is_file():
            raise CaptureError(f"missing XOLogic product search projection: {search_sql}")
        db.executescript(search_sql.read_text())
        db.commit()
    except Exception:
        db.close(); output.unlink(missing_ok=True); raise
    else:
        db.close()


def verify_cell(path: Path) -> dict[str,Any]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise CaptureError("integrity check failed")
        edition=db.execute("SELECT * FROM xologic_editions").fetchall()
        if len(edition)!=1: raise CaptureError("cell must contain one source edition")
        counts={name:int(db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]) for name in
                ("products","xologic_source_observations","source_properties","spec_occurrences","taxonomy_paths","product_links","product_resources")}
        if counts["products"]!=edition[0]["product_count"] or counts["xologic_source_observations"]!=edition[0]["source_observation_count"]:
            raise CaptureError("cell population differs from edition receipt")
        metadata = dict(db.execute("SELECT key,value FROM _metadata"))
        for relation in ("source_properties", "spec_occurrences", "product_links", "product_resources"):
            expected = metadata.get(f"expected_{relation}")
            if expected is None or counts[relation] != int(expected):
                raise CaptureError(f"{relation} population differs from sealed capture")
        forbidden={"_raw_chunks","_raw_sources","chunks_fts"}
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables & forbidden: raise CaptureError("generic retrieval tables are forbidden")
        product_count = counts["products"]
        search_document_count = int(db.execute(
            "SELECT count(*) FROM product_search_documents"
        ).fetchone()[0])
        search_index_count = int(db.execute(
            "SELECT count(*) FROM products_fts"
        ).fetchone()[0])
        dossier_count = int(db.execute(
            "SELECT count(*) FROM product_dossiers"
        ).fetchone()[0])
        dossier_payload_count = int(db.execute(
            "SELECT count(*) FROM product_dossier_payloads"
        ).fetchone()[0])
        product_identifier_count = int(db.execute(
            "SELECT count(*) FROM product_identifiers"
        ).fetchone()[0])
        if (search_document_count != product_count
                or search_index_count != product_count
                or dossier_count != product_count
                or dossier_payload_count != product_count):
            raise CaptureError(
                "product retrieval coverage differs from product population: "
                f"{search_document_count}/{search_index_count}/{dossier_count}/"
                f"{dossier_payload_count} "
                f"!= {product_count}"
            )
        if product_count and product_identifier_count == 0:
            raise CaptureError("product identifier projection is empty")
        orient = db.execute(
            "SELECT sql FROM _presets WHERE name='orient'"
        ).fetchone()
        required_orient_relations = {
            "resolved_spec_values", "products_fts", "product_identifiers",
            "product_dossiers", "product_dossier_payloads",
            "product_specification", "additional_product_details",
            "other_details", "images", "downloads", "options",
            "additional_options_that_would_change_other_selections",
            "collection_products", "related_products",
            "agent_product_status",
        }
        if orient is None or any(name not in orient[0] for name in required_orient_relations):
            raise CaptureError("@orient does not declare the enriched XOLogic surface")
        expected_orient = (MODULE_ROOT / "stock" / "presets" / "orient.sql").read_text()
        if orient[0] != expected_orient:
            raise CaptureError("installed @orient differs from the authored XOLogic contract")
        return {
            **counts,
            "product_search_documents": search_document_count,
            "products_fts": search_index_count,
            "product_dossiers": dossier_count,
            "product_dossier_payloads": dossier_payload_count,
            "product_identifiers": product_identifier_count,
            "integrity_check":"ok",
            "edition_id":edition[0]["edition_id"],
            "orient_sha256": _digest_bytes(orient[0].encode("utf-8")),
            "instructions_sha256": _digest_file(INSTRUCTIONS_PATH),
            "cell_sha256":_digest_file(path),
        }


def _build_receipt(capture: Path, verification: dict[str, Any]) -> dict[str, Any]:
    paths = _capture_paths(capture)
    capture_receipt = _verify_receipt(paths)
    count_names = (
        "products", "xologic_source_observations", "source_properties",
        "spec_occurrences", "taxonomy_paths",
        "product_links", "product_resources", "product_search_documents",
        "products_fts", "product_identifiers", "product_dossiers",
        "product_dossier_payloads",
    )
    counts = {name: int(verification[name]) for name in count_names}
    return {
        "schema_version": BUILD_RECEIPT_SCHEMA,
        "capture_receipt": capture_receipt["receipt_hash"],
        "transformation_sha256": _transformation_hash(),
        "cell_sha256": _sha256_uri(verification["cell_sha256"]),
        "edition_id": verification["edition_id"],
        "orient_sha256": _sha256_uri(verification["orient_sha256"]),
        "instructions_sha256": _sha256_uri(verification["instructions_sha256"]),
        "counts": counts,
    }


def build_cell(capture: Path, cell_path: Path, *, store_name: str | None=None,
               receipt_path: Path | None = None) -> dict[str,Any]:
    target=Path(cell_path); target.parent.mkdir(parents=True,exist_ok=True)
    if receipt_path is not None and Path(receipt_path).resolve() == target.resolve():
        raise CaptureError("build receipt path must differ from the SQLite artifact path")
    staging_root=Path(tempfile.mkdtemp(prefix=f".{target.stem}.xologic.",dir=target.parent))
    try:
        staging=staging_root/(target.name+".native")
        _build_database(Path(capture),staging,store_name)
        verification = verify_cell(staging)
        os.replace(staging,target)
        receipt = _build_receipt(Path(capture), verification)
        if receipt_path is not None:
            _write_json_atomic(Path(receipt_path), receipt)
        return receipt
    finally:
        shutil.rmtree(staging_root,ignore_errors=True)


def register_cell_path(name: str, path: Path, *, description: str | None=None) -> str:
    from flex.registry import register_cell
    return register_cell(name=name,path=path,cell_type="xologic",description=description,
                         lifecycle="refresh",refresh_interval=6*60*60,
                         refresh_module=REFRESH_MODULE,active=True,unlisted=False)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture",required=True); parser.add_argument("--cell",required=True)
    parser.add_argument("--store-name"); parser.add_argument("--register",action="store_true")
    parser.add_argument("--receipt")
    args=parser.parse_args(argv)
    target=Path(args.cell)
    receipt=build_cell(Path(args.capture),target,store_name=args.store_name,
                       receipt_path=Path(args.receipt) if args.receipt else None)
    if args.register: register_cell_path(target.stem,target)
    print(json.dumps(receipt,indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
