from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from modules.xologic.compile.refresh import refresh
from modules.xologic.compile.worker import CaptureError, build_cell, verify_cell


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _fixture(root: Path) -> Path:
    body = b"<html><body>fixture source</body></html>"
    digest = sha256(body).hexdigest()
    relative = f"raw/product_pdp/sha256/{digest[:2]}/{digest}.html"
    raw = root / relative
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(body)
    base = "https://shop.test/brand/acme/fixture/sku-V1-ABC"
    product = {
        "handle": "fixture", "title": "Fixture", "url": base,
        "sku": "ABC", "partnumber": "ABC", "vendor": "Acme", "vendor_id": "1",
        "tags": ["Pendants", "Down Pendants"], "variants": [{"price": "99.00"}],
        "image": "https://shop.test/images/abc.jpg", "body_html": "Fixture description",
    }
    _dump(root / "structured/products.json", {"products": [product]})
    prop = {"surface_ordinal": 1, "tag": "div", "property_attribute": "property",
            "property": "schema:sku", "value_attribute": "content", "value": "ABC",
            "locator": "html:structuredProperty[1]", "attributes": {"content": "ABC"},
            "raw_start_tag": '<div property="schema:sku" content="ABC">'}
    sources = []
    for suffix, url in (("base", base), ("fragment", base + "#itemDetailFinish")):
        sources.append({
            "source_id": "src_" + suffix, "source_namespace": "xologic",
            "object_kind": "product_pdp", "identity_key": url, "requested_url": url,
            "fetch_url": base, "final_url": base, "status": 200,
            "parser_identity": "parser/v1", "config_identity": "config/v1",
            "code_identity": "code/v1", "raw_descriptor": {
                "path": relative, "sha256": digest, "bytes": len(body)},
            "structured_properties": [prop],
        })
    occurrences = []
    native = [
        (1, "Item #", "ABC", "html:dataDetailRow[1]"),
        (2, "Finish", "Black", "html:additionalData[2]"),
        (3, "Finish", "Black", "html:additionalData[3]"),
    ]
    for source in sources:
        for order, label, value, locator in native:
            occurrences.append({
                "source_id": source["source_id"], "source_layer": "additionalData",
                "source_order": order, "surface_ordinal": order,
                "clean_label": label, "clean_value": value, "locator": locator,
                "raw_label": f"<div>{label}</div>", "raw_value": f"<div>{value}</div>",
                "row_classes": "dataDetailRow", "visibility_evidence": {"hidden": False},
            })
    relations = [
        {"source_id": s["source_id"], "kind": "variant", "target_url": base + "?finish=black",
         "locator": "html:link[1]", "scope": "query", "target_id": "variant:black"}
        for s in sources
    ]
    assets = [{"asset_id": "asset_image", "object_kind": "image",
               "url": "https://shop.test/images/abc.jpg", "source_ids": ["src_base", "src_fragment"],
               "locators": ["html:image[1]"], "scope": "query"}]
    artifacts = {"source_objects": sources, "spec_occurrences": occurrences,
                 "relations": relations, "asset_inventory": assets}
    hashes = {}
    for name, rows in artifacts.items():
        path = root / "evidence" / f"{name}.json"
        _dump(path, {"artifact": name, "version": 1, "rows": rows})
        hashes[name] = "sha256:" + _sha(path)
    _dump(root / "evidence/receipt.json", {
        "sealed": True, "complete": True, "receipt_hash": "sha256:fixture",
        "artifact_hashes": hashes,
        "artifact_counts": {name: len(rows) for name, rows in artifacts.items()},
    })
    return root


def _reseal(root: Path) -> None:
    artifacts = ("source_objects", "spec_occurrences", "relations", "asset_inventory")
    counts = {}
    hashes = {}
    for name in artifacts:
        path = root / "evidence" / f"{name}.json"
        rows = json.loads(path.read_text())["rows"]
        counts[name] = len(rows)
        hashes[name] = "sha256:" + _sha(path)
    _dump(root / "evidence/receipt.json", {
        "sealed": True, "complete": True, "receipt_hash": "sha256:fixture-resealed",
        "artifact_hashes": hashes, "artifact_counts": counts,
    })


def test_native_cell_deduplicates_content_not_observations(tmp_path, monkeypatch):
    capture = _fixture(tmp_path / "capture")
    target = tmp_path / "bes_lighting_xologic.db"
    receipt = build_cell(capture, target, store_name="BES Lighting")
    assert receipt["atomic_replace"] is True
    assert receipt["products"] == 1
    assert receipt["xologic_source_observations"] == 2
    assert receipt["xologic_content_objects"] == 1
    assert receipt["spec_occurrences"] == 3  # duplicate parse collapsed; two real Finish rows survive
    assert receipt["source_properties"] == 1
    assert receipt["taxonomy_paths"] == 2
    assert receipt["product_links"] == 1
    assert receipt["product_resources"] == 1

    with sqlite3.connect(target) as db:
        body, digest = db.execute("SELECT body,content_hash FROM xologic_content_objects").fetchone()
        assert sha256(body).hexdigest() == digest
        assert db.execute("SELECT count(*) FROM xologic_source_observations WHERE requested_url LIKE '%#itemDetailFinish'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM spec_occurrences WHERE label='Finish' AND value='Black'").fetchone()[0] == 2
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not tables & {"_raw_chunks", "_raw_sources", "chunks_fts"}
        child_tables = ("source_properties", "spec_occurrences", "product_links", "product_resources")
        for table in child_tables:
            assert "raw_json" not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

    from flex.engine import execute_preset
    with sqlite3.connect(target) as db:
        db.row_factory = sqlite3.Row
        result = json.loads(execute_preset(db, "@orient", materializer=lambda _db, sql: sql,
                                           cell_name="bes_lighting_xologic"))
    assert any(row.get("query") == "shape" for row in result)
    assert any(row.get("query") == "query_surface" for row in result)

    dry = refresh(str(target), graph=True, dry_run=True, since_days=1)
    assert dry["dry_run"] is True and dry["seed_products"] == 1 and dry["products"] == 1
    assert verify_cell(target)["integrity_check"] == "ok"


def test_atomic_replacement_rebuilds_same_logical_cell(tmp_path):
    capture = _fixture(tmp_path / "capture")
    target = tmp_path / "catalogue.db"
    first = build_cell(capture, target, store_name="One")
    second = build_cell(capture, target, store_name="Two")
    assert first["cell_sha256"] != second["cell_sha256"]
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT store_name FROM xologic_editions").fetchone()[0] == "Two"


def test_tampered_raw_body_fails_closed_without_replacing_cell(tmp_path):
    capture = _fixture(tmp_path / "capture")
    target = tmp_path / "catalogue.db"
    build_cell(capture, target)
    before = target.read_bytes()
    descriptor = json.loads((capture / "evidence/source_objects.json").read_text())["rows"][0]["raw_descriptor"]
    (capture / descriptor["path"]).write_bytes(b"tampered")
    with pytest.raises(CaptureError, match="hash/size mismatch"):
        build_cell(capture, target)
    assert target.read_bytes() == before


def test_source_only_option_sku_is_promoted_without_python_normalization(tmp_path):
    capture = _fixture(tmp_path / "capture")
    option_url = "https://shop.test/brand/acme/fixture-white/sku-V1-ABC-WH"
    body = b"<html><body>white option source</body></html>"
    digest = sha256(body).hexdigest()
    relative = f"raw/product_pdp/sha256/{digest[:2]}/{digest}.html"
    raw = capture / relative
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(body)
    source_path = capture / "evidence/source_objects.json"
    payload = json.loads(source_path.read_text())
    payload["rows"].append({
        "source_id": "src_white", "source_namespace": "xologic",
        "object_kind": "product_pdp", "identity_key": option_url,
        "requested_url": option_url, "fetch_url": option_url, "final_url": option_url,
        "status": 200, "parser_identity": "parser/v1", "config_identity": "config/v1",
        "code_identity": "code/v1",
        "raw_descriptor": {"path": relative, "sha256": digest, "bytes": len(body)},
        "structured_properties": [
            {"surface_ordinal": 1, "tag": "meta", "property": "schema:name",
             "value": "Fixture - White", "locator": "html:property[1]"},
            {"surface_ordinal": 2, "tag": "meta", "property": "schema:sku",
             "value": "ABC-WH", "locator": "html:property[2]"},
            {"surface_ordinal": 3, "tag": "meta", "property": "schema:price",
             "value": "109.00", "locator": "html:property[3]"},
        ],
    })
    _dump(source_path, payload)
    occurrence_path = capture / "evidence/spec_occurrences.json"
    occurrences = json.loads(occurrence_path.read_text())
    for order, label, value in ((1, "Category", "Pendants"),
                                (2, "Subcategory", "Down Pendants"),
                                (3, "Finish", "White")):
        occurrences["rows"].append({
            "source_id": "src_white", "source_layer": "additionalData",
            "source_order": order, "surface_ordinal": order,
            "clean_label": label, "clean_value": value,
            "locator": f"html:additionalData[{order}]",
            "visibility_evidence": {"hidden": False},
        })
    _dump(occurrence_path, occurrences)
    _reseal(capture)

    from modules.xologic.compile.worker import inspect_capture
    assert inspect_capture(capture)["products"] == 2

    target = tmp_path / "catalogue.db"
    receipt = build_cell(capture, target)
    assert receipt["products"] == 2
    with sqlite3.connect(target) as db:
        projection, sku = db.execute(
            "SELECT json_extract(product_json,'$.source_projection'),sku "
            "FROM products WHERE url=?", (option_url,)
        ).fetchone()
        assert (projection, sku) == ("xologic-rdfa", "ABC-WH")
        assert db.execute(
            "SELECT group_concat(label,'|') FROM taxonomy_paths "
            "WHERE product_ref=(SELECT product_ref FROM products WHERE url=?) ORDER BY depth",
            (option_url,),
        ).fetchone()[0] == "Pendants|Down Pendants"
