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
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urldefrag

FORMAT = "xologic-native-flex/v1"
BUILD_RECEIPT_SCHEMA = "xologic-native-flex-build/v1"
REFRESH_MODULE = "flex.modules.xologic.compile.refresh"
MODULE_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_CAPTURE_ARTIFACTS = {
    "source_objects", "spec_occurrences", "relations", "asset_inventory",
}
MODULE_IDENTITY_PATHS = (
    "compile/worker.py",
    "compile/recipes.py",
    "stock/presets/orient.sql",
    "stock/sql/lighting_projection.sql",
    "stock/sql/product_search.sql",
    "stock/instructions.md",
)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE xologic_editions(
  edition_id TEXT PRIMARY KEY, capture_path TEXT NOT NULL, store_name TEXT,
  evidence_receipt TEXT NOT NULL, products_sha256 TEXT NOT NULL,
  product_count INTEGER NOT NULL, source_observation_count INTEGER NOT NULL
);
CREATE TABLE xologic_content_objects(
  content_hash TEXT PRIMARY KEY, mime_type TEXT NOT NULL, byte_count INTEGER NOT NULL,
  body BLOB NOT NULL CHECK(length(body)=byte_count)
);
CREATE TABLE xologic_source_observations(
  source_id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES xologic_editions,
  content_hash TEXT NOT NULL REFERENCES xologic_content_objects,
  source_namespace TEXT, object_kind TEXT, identity_key TEXT,
  requested_url TEXT NOT NULL, fetch_url TEXT, final_url TEXT, status INTEGER,
  parser_identity TEXT, config_identity TEXT, code_identity TEXT,
  descriptor_path TEXT NOT NULL
);
CREATE TABLE products(
  product_ref TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES xologic_editions,
  source_order INTEGER NOT NULL, url TEXT NOT NULL UNIQUE, handle TEXT,
  sku TEXT, partnumber TEXT, gtin TEXT, title TEXT NOT NULL, vendor TEXT,
  vendor_id TEXT, price TEXT, image_url TEXT, body_html TEXT,
  product_json TEXT NOT NULL
);
CREATE TABLE product_source_observations(
  product_ref TEXT NOT NULL REFERENCES products, source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  PRIMARY KEY(product_ref,source_id)
);
CREATE TABLE source_properties(
  property_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT NOT NULL REFERENCES xologic_content_objects,
  source_order INTEGER NOT NULL, tag TEXT, property_attribute TEXT,
  property TEXT, value_attribute TEXT, value TEXT, locator TEXT,
  attributes_json TEXT, raw_start_tag TEXT
);
CREATE TABLE spec_occurrences(
  occurrence_id TEXT PRIMARY KEY, product_ref TEXT REFERENCES products,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT NOT NULL REFERENCES xologic_content_objects,
  source_layer TEXT, source_order INTEGER NOT NULL, surface_ordinal INTEGER,
  label TEXT NOT NULL, value TEXT NOT NULL, locator TEXT,
  raw_label TEXT, raw_value TEXT, row_classes TEXT,
  visible INTEGER, visibility_json TEXT
);
CREATE TABLE taxonomy_paths(
  product_ref TEXT NOT NULL REFERENCES products, depth INTEGER NOT NULL, label TEXT NOT NULL,
  PRIMARY KEY(product_ref,depth,label)
);
CREATE TABLE product_links(
  link_id TEXT PRIMARY KEY, product_ref TEXT REFERENCES products,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT REFERENCES xologic_content_objects, kind TEXT NOT NULL,
  url TEXT NOT NULL, locator TEXT, scope TEXT, raw_href TEXT,
  target_id TEXT, target_source_id TEXT, target_object_kind TEXT
);
CREATE TABLE product_resources(
  resource_id TEXT PRIMARY KEY, product_ref TEXT REFERENCES products,
  source_id TEXT NOT NULL REFERENCES xologic_source_observations,
  content_hash TEXT REFERENCES xologic_content_objects, kind TEXT NOT NULL,
  url TEXT NOT NULL, asset_id TEXT, raw_href TEXT, locator TEXT,
  locators_json TEXT NOT NULL, scope TEXT
);
CREATE INDEX ix_source_requested ON xologic_source_observations(requested_url);
CREATE INDEX ix_source_content ON xologic_source_observations(content_hash);
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


def _module_identity() -> dict[str, Any]:
    file_hashes: dict[str, str] = {}
    for relative in MODULE_IDENTITY_PATHS:
        path = MODULE_ROOT / relative
        if not path.is_file():
            raise CaptureError(f"missing XOLogic module source: {path}")
        file_hashes[relative] = _sha256_uri(_digest_file(path))
    source_hash = _sha256_uri(
        _digest_bytes(_canonical(file_hashes).encode("utf-8"))
    )
    git_head: str | None = None
    git_dirty: bool | None = None
    try:
        repo_root = Path(subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=MODULE_ROOT,
            check=True, capture_output=True, text=True,
        ).stdout.strip())
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        identity_paths = [
            str((MODULE_ROOT / relative).relative_to(repo_root))
            for relative in MODULE_IDENTITY_PATHS
        ]
        git_dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--", *identity_paths], cwd=repo_root,
            check=True, capture_output=True, text=True,
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return {
        "format": FORMAT,
        "module_git_head": git_head,
        "module_dirty": git_dirty,
        "module_file_hashes": file_hashes,
        "module_source_hash": source_hash,
    }


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
    if (receipt.get("schema_version") != "xologic-lossless/v1"
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


def _product_ref(product: dict[str, Any]) -> str:
    url = product.get("url")
    if not isinstance(url,str) or not url:
        raise CaptureError("product has no URL")
    return _stable_id("product", url)


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
    ambiguous: set[str] = set()
    for product in products:
        ref = _product_ref(product)
        url = str(product["url"])
        exact[url] = ref
        key = urldefrag(url)[0]
        if key in base and base[key] != ref:
            ambiguous.add(key)
        else:
            base[key] = ref
    for key in ambiguous:
        base.pop(key,None)
    return exact,base


def _map_source(row: dict[str,Any], exact: dict[str,str], base: dict[str,str]) -> str | None:
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


def _build_database(capture: Path, output: Path, store_name: str | None) -> dict[str,Any]:
    paths = _capture_paths(capture)
    receipt = _verify_receipt(paths)
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

    db = sqlite3.connect(output)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        from flex.envelope import install_cell_core
        install_cell_core(db)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO xologic_editions VALUES(?,?,?,?,?,?,?)",
                   (edition_id,str(paths["root"]),store_name,receipt_id,products_sha,len(products),len(source_rows)))
        metadata = {
            "cell_type":"xologic","provider":"xologic",
            "profile":"commerce-native","schema_version":FORMAT,
            "description":f"{store_name or 'XOLogic'} source-faithful native catalogue",
            "capture_path":str(paths["root"]),"store_name":store_name or "XOLogic",
            "evidence_receipt":receipt_id,"products_sha256":products_sha,
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
            db.execute("""INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ref,edition_id,order,url,product.get("handle"),product.get("sku"),
                 product.get("partnumber"),product.get("gtin"),title,product.get("vendor"),
                 None if product.get("vendor_id") is None else str(product.get("vendor_id")),
                 _price(product),product.get("image"),product.get("body_html"),_canonical(product)))
            for depth,label in enumerate(product.get("tags") or [],1):
                db.execute("INSERT OR IGNORE INTO taxonomy_paths VALUES(?,?,?)",(ref,depth,str(label)))

        source_hash: dict[str,str] = {}
        source_product: dict[str,str|None] = {}
        bodies: dict[str,bytes] = {}
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
            body = bodies.get(expected)
            if body is None:
                body = raw_path.read_bytes()
                if _digest_bytes(body) != expected or (descriptor.get("bytes") is not None and len(body)!=int(descriptor["bytes"])):
                    raise CaptureError(f"source body hash/size mismatch: {relative}")
                bodies[expected]=body
                db.execute("INSERT INTO xologic_content_objects VALUES(?,?,?,?)",
                           (expected,"text/html",len(body),body))
            source_hash[source_id]=expected
            mapped=_map_source(row,exact_urls,base_urls)
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
                db.execute("INSERT OR IGNORE INTO source_properties VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid,source_id,expected,int(prop.get("surface_ordinal") or 0),prop.get("tag"),prop.get("property_attribute"),
                     prop.get("property"),prop.get("value_attribute"),prop.get("value"),prop.get("locator"),
                     _canonical(prop.get("attributes") or {}),prop.get("raw_start_tag")))

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
            SELECT r.final_url,p.name,p.sku,p.mpn,p.gtin,p.price,p.image,p.description
            FROM ranked r JOIN props p USING(source_id)
            WHERE r.source_rank=1 AND p.name IS NOT NULL
              AND NOT EXISTS(SELECT 1 FROM products x WHERE x.url=r.final_url)
            ORDER BY r.final_url
        """).fetchall()
        next_order = int(db.execute("SELECT COALESCE(max(source_order),0) FROM products").fetchone()[0])
        for offset,(url,name,sku,mpn,gtin,price,image,description) in enumerate(additions,1):
            projected = {"url":url,"title":name,"sku":sku,"partnumber":mpn or sku,
                         "gtin":gtin,"variants":[{"price":price}] if price is not None else [],
                         "image":image,"body_html":description,"source_projection":"xologic-rdfa"}
            db.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_stable_id("product",url),edition_id,next_order+offset,url,url,sku,mpn or sku,gtin,
                 name,None,None,price,image,description,_canonical(projected)))

        product_pairs=db.execute("SELECT url,product_ref FROM products").fetchall()
        exact_urls={url:ref for url,ref in product_pairs}
        base_urls={}
        ambiguous=set()
        for url,ref in product_pairs:
            key=urldefrag(url)[0]
            if key in base_urls and base_urls[key]!=ref: ambiguous.add(key)
            else: base_urls[key]=ref
        for key in ambiguous: base_urls.pop(key,None)
        source_product={str(row["source_id"]):_map_source(row,exact_urls,base_urls) for row in source_rows}
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
            oid=_stable_id("occurrence",*signature)
            visible=(row.get("visibility_evidence") or {}).get("hidden") is False
            db.execute("INSERT OR IGNORE INTO spec_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid,source_product.get(str(sid)),str(sid),content,row.get("source_layer"),int(row.get("source_order") or 0),
                 row.get("surface_ordinal"),str(label),str(value),row.get("locator"),row.get("raw_label"),row.get("raw_value"),
                 row.get("row_classes"),int(visible),_canonical(row.get("visibility_evidence") or {})))

        for row in relation_rows:
            sid=str(row.get("source_id") or ""); content=source_hash.get(sid); url=row.get("target_url")
            if not content or not isinstance(url,str) or not url: continue
            product_ref=source_product.get(sid)
            signature=(sid,product_ref,content,row.get("kind"),url,row.get("locator"),row.get("scope"),row.get("target_id"))
            db.execute("INSERT OR IGNORE INTO product_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (_stable_id("link",*signature),product_ref,sid,content,str(row.get("kind") or "link"),url,row.get("locator"),
                 row.get("scope"),row.get("raw_href"),row.get("target_id"),row.get("target_source_id"),row.get("target_object_kind")))

        for row in asset_rows:
            url=row.get("url")
            if not isinstance(url,str) or not url: continue
            source_ids=[str(v) for v in (row.get("source_ids") or [])] or [""]
            mapped=[(sid,source_product.get(sid),source_hash.get(sid)) for sid in source_ids
                    if source_hash.get(sid)]
            for sid,product_ref,content in sorted(mapped,key=lambda item:(item[0],item[1] or "",item[2] or "")):
                signature=(sid,product_ref,content,row.get("object_kind"),url,row.get("asset_id"))
                locators=[str(locator) for locator in (row.get("locators") or [])]
                db.execute("INSERT OR IGNORE INTO product_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (_stable_id("resource",*signature),product_ref,sid,content,str(row.get("object_kind") or "resource"),url,
                     row.get("asset_id"),row.get("raw_href"),locators[0] if locators else None,
                     _canonical(locators),row.get("scope")))

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
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CaptureError("SQLite integrity check failed")
    except Exception:
        db.close(); output.unlink(missing_ok=True); raise
    else:
        db.close()
    return verify_cell(output)


def verify_cell(path: Path) -> dict[str,Any]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise CaptureError("integrity check failed")
        edition=db.execute("SELECT * FROM xologic_editions").fetchall()
        if len(edition)!=1: raise CaptureError("cell must contain one source edition")
        counts={name:int(db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]) for name in
                ("products","xologic_source_observations","xologic_content_objects","source_properties","spec_occurrences","taxonomy_paths","product_links","product_resources")}
        if counts["products"]!=edition[0]["product_count"] or counts["xologic_source_observations"]!=edition[0]["source_observation_count"]:
            raise CaptureError("cell population differs from edition receipt")
        metadata = dict(db.execute("SELECT key,value FROM _metadata"))
        for relation in ("source_properties", "spec_occurrences", "product_links", "product_resources"):
            expected = metadata.get(f"expected_{relation}")
            if expected is None or counts[relation] != int(expected):
                raise CaptureError(f"{relation} population differs from sealed capture")
        for row in db.execute("SELECT content_hash,byte_count,body FROM xologic_content_objects"):
            if len(row["body"])!=row["byte_count"] or _digest_bytes(bytes(row["body"]))!=row["content_hash"]:
                raise CaptureError("content object hash/size differs")
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
        }
        if orient is None or any(name not in orient[0] for name in required_orient_relations):
            raise CaptureError("@orient does not declare the enriched XOLogic surface")
        expected_orient = (MODULE_ROOT / "stock" / "presets" / "orient.sql").read_text()
        if orient[0] != expected_orient:
            raise CaptureError("installed @orient differs from the authored XOLogic contract")
        page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
        return {
            **counts,
            "product_search_documents": search_document_count,
            "products_fts": search_index_count,
            "product_dossiers": dossier_count,
            "product_dossier_payloads": dossier_payload_count,
            "product_identifiers": product_identifier_count,
            "integrity_check":"ok",
            "retrieval_denominator_match": True,
            "orient_declares_enriched_surface": True,
            "sqlite_page_count": page_count,
            "sqlite_page_size": page_size,
            "edition_id":edition[0]["edition_id"],
            "products_sha256": edition[0]["products_sha256"],
            "orient_sha256": _digest_bytes(orient[0].encode("utf-8")),
            "cell_sha256":_digest_file(path),
        }


def _build_receipt(capture: Path, cell_path: Path, verification: dict[str, Any],
                   *, store_name: str | None) -> dict[str, Any]:
    paths = _capture_paths(capture)
    capture_receipt = _verify_receipt(paths)
    module = _module_identity()
    count_names = (
        "products", "xologic_source_observations", "xologic_content_objects",
        "source_properties", "spec_occurrences", "taxonomy_paths",
        "product_links", "product_resources", "product_search_documents",
        "products_fts", "product_identifiers", "product_dossiers",
        "product_dossier_payloads",
    )
    counts = {name: int(verification[name]) for name in count_names}
    receipt: dict[str, Any] = {
        "schema_version": BUILD_RECEIPT_SCHEMA,
        "locations": {
            "capture_path": str(paths["root"]),
            "cell_path": str(Path(cell_path).resolve()),
        },
        "input": {
            "capture_receipt_hash": capture_receipt["receipt_hash"],
            "capture_receipt_file_sha256": _sha256_uri(_digest_file(paths["receipt"])),
            "capture_schema_version": capture_receipt.get("schema_version"),
            "capture_artifact_hashes": capture_receipt.get("artifact_hashes") or {},
            "capture_artifact_counts": capture_receipt.get("artifact_counts") or {},
            "raw_inventory_hash": capture_receipt.get("raw_inventory_hash"),
            "raw_inventory_count": capture_receipt.get("raw_inventory_count"),
            "factory_implementation_identities": (
                capture_receipt.get("implementation_identities") or {}
            ),
            "factory_implementation_manifest_hash": (
                capture_receipt.get("implementation_manifest_hash")
            ),
        },
        "module": module,
        "environment": {
            "python_version": ".".join(str(value) for value in sys.version_info[:3]),
            "sqlite_version": sqlite3.sqlite_version,
        },
        "output": {
            "cell_sha256": _sha256_uri(verification["cell_sha256"]),
            "sqlite_page_count": verification["sqlite_page_count"],
            "sqlite_page_size": verification["sqlite_page_size"],
            "integrity_check": verification["integrity_check"],
            "edition_id": verification["edition_id"],
            "products_sha256": _sha256_uri(verification["products_sha256"]),
            "orient_sha256": _sha256_uri(verification["orient_sha256"]),
            "store_name": store_name or "XOLogic",
        },
        "counts": counts,
        "validation": {
            "native_population_match": True,
            "retrieval_denominator_match": verification["retrieval_denominator_match"],
            "orient_declares_enriched_surface": (
                verification["orient_declares_enriched_surface"]
            ),
        },
    }
    identity = {
        "schema_version": receipt["schema_version"],
        "input": receipt["input"],
        "module": {
            "format": module["format"],
            "module_file_hashes": module["module_file_hashes"],
            "module_source_hash": module["module_source_hash"],
        },
        "environment": receipt["environment"],
        "output": receipt["output"],
        "counts": receipt["counts"],
        "validation": receipt["validation"],
    }
    receipt["content_identity_sha256"] = _sha256_uri(
        _digest_bytes(_canonical(identity).encode("utf-8"))
    )
    return {
        **receipt,
        **verification,
        "cell": str(Path(cell_path)),
        "atomic_replace": True,
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
        verify_cell(staging)
        os.replace(staging,target)
        receipt = _build_receipt(
            Path(capture), target, verify_cell(target), store_name=store_name
        )
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
