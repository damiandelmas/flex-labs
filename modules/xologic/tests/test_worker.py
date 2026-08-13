from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

import pytest

from modules.xologic.compile.refresh import refresh
from modules.xologic.compile.recipes import (
    EXPECTED_SQL_SHA256,
    load_recipes,
)
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
        (1, "standardData", "Item #", "ABC"),
        (2, "standardData", "Finish", "Black"),
        (3, "additionalData", "Finish", "Black"),
        (4, "standardData", "Voltage", "100-120"),
        (5, "additionalData", "Voltage", "120 V"),
        (6, "additionalData", "Kelvin", "2700/3000/4000/5000"),
        (7, "additionalData", "Wattage", "9 W"),
        (8, "additionalData", "Max Wattage", "60 W"),
        (9, "additionalData", "Lumens / Light Output", "550"),
        (10, "additionalData", "Bulb CRI", "90+"),
        (11, "additionalData", "IP Rating", "IP65"),
        (12, "additionalData", "Dimmable", "Yes-Dimmable"),
        (13, "additionalData", "Bulb Dimmable", "No"),
        (14, "additionalData", "Dimming", "0-10V"),
    ]
    for source in sources:
        for order, layer, label, value in native:
            occurrences.append({
                "source_id": source["source_id"], "source_layer": layer,
                "source_order": order, "surface_ordinal": order,
                "clean_label": label, "clean_value": value,
                "locator": f"html:{layer}[{order}]",
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
               "locators": ["html:image[1]", "html:gallery[1]"], "scope": "query"}]
    artifacts = {"source_objects": sources, "spec_occurrences": occurrences,
                 "relations": relations, "asset_inventory": assets}
    hashes = {}
    for name, rows in artifacts.items():
        path = root / "evidence" / f"{name}.json"
        _dump(path, {"artifact": name, "version": 1, "rows": rows})
        hashes[name] = "sha256:" + _sha(path)
    _dump(root / "evidence/receipt.json", {
        "schema_version": "xologic-lossless/v1",
        "sealed": True, "complete": True, "frontier_exhausted": True,
        "receipt_hash": "sha256:fixture",
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
        "schema_version": "xologic-lossless/v1",
        "sealed": True, "complete": True, "frontier_exhausted": True,
        "receipt_hash": "sha256:fixture-resealed",
        "artifact_hashes": hashes, "artifact_counts": counts,
    })


def test_native_cell_deduplicates_content_not_observations(tmp_path, monkeypatch):
    capture = _fixture(tmp_path / "capture")
    target = tmp_path / "bes_lighting_xologic.db"
    receipt = build_cell(capture, target, store_name="BES Lighting")
    counts = receipt["counts"]
    assert counts["products"] == 1
    assert counts["xologic_source_observations"] == 2
    assert counts["xologic_content_objects"] == 1
    assert counts["spec_occurrences"] == 28  # every source observation remains addressable
    assert counts["source_properties"] == 2
    assert counts["taxonomy_paths"] == 2
    assert counts["product_links"] == 2
    assert counts["product_resources"] == 2
    assert counts["product_search_documents"] == 1
    assert counts["products_fts"] == 1
    assert counts["product_dossiers"] == 1
    assert counts["product_dossier_payloads"] == 1
    assert counts["product_identifiers"] == 3

    with sqlite3.connect(target) as db:
        body, digest = db.execute("SELECT body,content_hash FROM xologic_content_objects").fetchone()
        assert sha256(body).hexdigest() == digest
        assert db.execute("SELECT count(*) FROM xologic_source_observations WHERE requested_url LIKE '%#itemDetailFinish'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM spec_occurrences WHERE label='Finish' AND value='Black'").fetchone()[0] == 4
        assert json.loads(db.execute(
            "SELECT locators_json FROM product_resources"
        ).fetchone()[0]) == ["html:image[1]", "html:gallery[1]"]
        objects = {
            (row[0], row[1]) for row in db.execute(
                "SELECT type,name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        }
        assert objects == {
            ("table", "_metadata"), ("table", "_presets"),
            ("table", "resolved_spec_values"), ("table", "spec_field_map"),
            ("table", "product_links"), ("table", "product_resources"),
            ("table", "product_search_documents"),
            ("table", "product_identifiers"),
            ("table", "product_dossier_payloads"),
            ("table", "product_source_observations"), ("table", "products"),
            ("table", "products_fts"), ("table", "products_fts_config"),
            ("table", "products_fts_data"), ("table", "products_fts_docsize"),
            ("table", "products_fts_idx"),
            ("table", "source_properties"), ("table", "spec_occurrences"),
            ("table", "taxonomy_paths"), ("table", "xologic_content_objects"),
            ("table", "xologic_editions"), ("table", "xologic_source_observations"),
            ("view", "_meta"), ("view", "preferred_spec_occurrences"),
            ("view", "product_dossiers"),
            ("view", "resolved_products"), ("view", "spec_conflicts"),
            ("view", "spec_occurrence_preference"),
            ("trigger", "_meta_delete"), ("trigger", "_meta_insert"),
            ("trigger", "_meta_update"),
            ("trigger", "product_search_documents_ad"),
            ("trigger", "product_search_documents_ai"),
            ("trigger", "product_search_documents_au"),
            ("index", "ix_links_product"), ("index", "ix_resources_product"),
            ("index", "ix_product_dossier_payloads_product"),
            ("index", "ix_product_identifiers_lookup"),
            ("index", "ix_resolved_spec_values_field"),
            ("index", "ix_resolved_spec_values_occurrence"),
            ("index", "ix_resolved_spec_values_product"),
            ("index", "ix_source_content"), ("index", "ix_source_requested"),
            ("index", "ix_spec_lookup"), ("index", "ix_spec_product"),
            ("index", "ix_spec_field_map_field"),
            ("index", "ix_taxonomy_lookup"),
        }
        resolved = db.execute(
            "SELECT category,subcategory,finish,price_min,price_max,voltage_min,voltage_max,"
            "cct_min,cct_max,cct_selectable,wattage_min,wattage_max,max_wattage_min,"
            "max_wattage_max,lumens,bulb_cri,ip_rating,fixture_dimmable,bulb_dimmable,"
            "dimming_protocols FROM resolved_products"
        ).fetchone()
        assert resolved == (
            "Pendants", "Down Pendants", "Black", 99.0, 99.0, 120.0, 120.0,
            2700.0, 5000.0, 1, 9.0, 9.0, 60.0, 60.0, 550.0, 90.0,
            65.0, 1, 0, "0-10V",
        )
        voltage_rows = db.execute(
            "SELECT raw_value,sum(is_preferred),min(has_conflict),max(has_conflict) "
            "FROM resolved_spec_values WHERE canonical_field='voltage' GROUP BY raw_value"
        ).fetchall()
        assert voltage_rows == [("100-120", 0, 1, 1), ("120 V", 1, 1, 1)]
        assert db.execute(
            "SELECT count(*) FROM spec_occurrences WHERE label IN "
            "('Voltage','Kelvin','Wattage','Max Wattage','Lumens / Light Output',"
            "'Bulb CRI','IP Rating','Dimmable','Bulb Dimmable','Dimming')"
        ).fetchone()[0] == 22
        assert {row[0] for row in db.execute("SELECT name FROM _presets")} == {"orient"}
        metadata = dict(db.execute("SELECT key,value FROM _metadata"))
        assert metadata["provider"] == "xologic"
        assert metadata["profile"] == "commerce-native"
        assert metadata["authority"] == "provider-native-capture"
        assert metadata["lifecycle"] == "refresh"
        capabilities = json.loads(metadata["capabilities"])
        assert capabilities["native_products"] == "present"
        assert capabilities["source_recovery"] == "present"
        assert capabilities["text_retrieval"] == "products_fts"
        assert capabilities["text_embeddings"] == "absent"
        assert capabilities["image_embeddings"] == "absent"
        assert capabilities["relationship_interop"] == "absent"
        assert capabilities["tree_navigation"] == "absent"
        assert capabilities["operations_log"] == "absent"
        child_tables = ("source_properties", "spec_occurrences", "product_links", "product_resources")
        for table in child_tables:
            assert "raw_json" not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        search_row = db.execute(
            "SELECT d.product_ref,p.title "
            "FROM products_fts "
            "JOIN product_search_documents d ON d.rowid=products_fts.rowid "
            "JOIN products p ON p.product_ref=d.product_ref "
            "WHERE products_fts MATCH 'ABC*'"
        ).fetchone()
        assert search_row is not None and search_row[1] == "Fixture"
        assert db.execute(
            "SELECT count(*) FROM products_fts "
            "WHERE products_fts MATCH 'identifiers : \"ABC\"'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT count(DISTINCT product_ref) FROM product_identifiers "
            "WHERE normalized_identifier LIKE '%abc%'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT group_concat(identifier_kind,',') FROM ("
            "SELECT identifier_kind FROM product_identifiers ORDER BY identifier_kind)"
        ).fetchone()[0] == "handle,partnumber,sku"
        assert "Pendants" in db.execute(
            "SELECT taxonomy FROM product_search_documents"
        ).fetchone()[0]
        assert "Finish: Black" in db.execute(
            "SELECT specifications FROM product_search_documents"
        ).fetchone()[0]
        dossier = db.execute(
            "SELECT sku,finish,taxonomy_json,specifications_json,"
            "conflicts_json,variants_json,documents_json FROM product_dossiers"
        ).fetchone()
        assert dossier[0:2] == ("ABC", "Black")
        assert {row[1] for row in json.loads(dossier[2])} == {
            "Pendants", "Down Pendants"
        }
        assert len(json.loads(dossier[3])) == 14
        assert all(row[4] == 2 for row in json.loads(dossier[3]))
        assert any(
            row[0] == "Voltage" for row in json.loads(dossier[4])
        )
        assert len(json.loads(dossier[5])) == 1
        assert len(json.loads(dossier[6])) == 0
        payload = db.execute(
            "SELECT dossier_json FROM product_dossier_payloads"
        ).fetchone()[0]
        decoded = json.loads(payload)
        assert decoded["sku"] == "ABC"
        assert decoded["finish"] == "Black"
        assert len(decoded["specifications"]) == 14

    from flex.engine import execute_preset
    with sqlite3.connect(target) as db:
        db.row_factory = sqlite3.Row
        result = json.loads(execute_preset(db, "@orient", materializer=lambda _db, sql: sql,
                                           cell_name="bes_lighting_xologic"))
    assert any(row.get("query") == "shape" for row in result)
    assert any(row.get("query") == "capabilities" for row in result)
    assert any(row.get("query") == "query_surface" for row in result)
    query_surface = next(row for row in result if row.get("query") == "query_surface")["results"]
    assert any(row.get("name") == "resolved_spec_values" for row in query_surface)
    assert any(row.get("name") == "products_fts" for row in query_surface)
    assert any(row.get("name") == "product_dossiers" for row in query_surface)
    assert any(row.get("name") == "product_identifiers" for row in query_surface)
    assert "Fixture" not in json.dumps(result)

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


def test_projection_fails_closed_on_ambiguous_or_misattributed_values(tmp_path):
    capture = _fixture(tmp_path / "capture")
    path = capture / "evidence/spec_occurrences.json"
    payload = json.loads(path.read_text())
    replacements = {
        "Voltage": "120/277V",
        "Kelvin": "27000",
        "Wattage": "60W equivalent",
        "Bulb CRI": ">90",
    }
    for row in payload["rows"]:
        label = row.get("clean_label")
        if label in replacements:
            row["clean_value"] = replacements[label]
            row["raw_value"] = f"<div>{replacements[label]}</div>"
    _dump(path, payload)
    _reseal(capture)

    target = tmp_path / "ambiguous.db"
    build_cell(capture, target)
    with sqlite3.connect(target) as db:
        assert db.execute(
            "SELECT voltage_min,voltage_max,cct_min,cct_max,wattage_min,wattage_max,"
            "bulb_cri FROM resolved_products"
        ).fetchone() == (None, None, None, None, None, None, 90.0)
        assert db.execute(
            "SELECT count(*) FROM resolved_spec_values "
            "WHERE canonical_field IN ('voltage','cct','wattage') "
            "AND parse_status IN ('ambiguous','unparsed')"
        ).fetchone()[0] == 8
        assert db.execute(
            "SELECT numeric_min,numeric_max,parse_status FROM resolved_spec_values "
            "WHERE canonical_field='bulb_cri' AND is_preferred=1"
        ).fetchone() == (90.0, None, "lower_bound")


def test_identical_rebuild_is_byte_stable(tmp_path):
    capture = _fixture(tmp_path / "capture")
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    first = build_cell(capture, first_path, store_name="Same")
    second = build_cell(capture, second_path, store_name="Same")
    assert first["cell_sha256"] == second["cell_sha256"]
    assert first_path.read_bytes() == second_path.read_bytes()


def test_build_receipt_identifies_capture_transform_and_output(tmp_path):
    capture = _fixture(tmp_path / "capture")
    target = tmp_path / "catalogue.db"
    receipt_path = tmp_path / "catalogue.receipt.json"
    receipt = build_cell(
        capture, target, store_name="Receipt Test", receipt_path=receipt_path
    )
    assert json.loads(receipt_path.read_text()) == receipt
    assert set(receipt) == {
        "schema_version", "capture_receipt", "transformation_sha256",
        "cell_sha256", "edition_id", "orient_sha256", "counts",
    }
    assert receipt["schema_version"] == "xologic-native-flex-build/v2"
    assert receipt["capture_receipt"] == "sha256:fixture"
    assert receipt["transformation_sha256"].startswith("sha256:")
    assert receipt["cell_sha256"] == "sha256:" + _sha(target)
    assert receipt["orient_sha256"].startswith("sha256:")
    assert receipt["counts"]["products"] == 1
    with pytest.raises(CaptureError, match="must differ"):
        build_cell(capture, target, receipt_path=target)


def test_authored_recipe_contract_has_one_stable_query_per_heading():
    recipes, digest = load_recipes()
    assert len(recipes) == 15
    assert digest == EXPECTED_SQL_SHA256
    assert [
        sorted(set(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", sql)))
        for sql in recipes
    ] == [
        ["identifier"], ["terms"], ["max_price", "terms"],
        ["excluded_vendor", "max_price", "terms"], ["terms"],
        ["max_price", "minimum_lumens", "required_voltage", "terms"],
        ["max_price"], ["max_price"], ["allow_transitional"],
        ["product_ref"], ["product_refs_json"], [],
        ["specification_terms", "title_terms"], ["max_price"],
        [
            "excluded_vendor", "new_price_ceiling", "original_terms",
            "required_finish", "required_style", "required_type",
            "shown_product_refs_json",
        ],
    ]


def test_registered_install_renders_the_current_receipt_contract(tmp_path, monkeypatch):
    from flex import registry
    from modules.xologic import install
    from modules.xologic.compile import worker

    cells = tmp_path / "cells"
    monkeypatch.setattr(registry, "CELLS_DIR", cells)
    registered = []
    monkeypatch.setattr(
        worker, "register_cell_path",
        lambda name, path, description=None: registered.append((name, path)),
    )

    class Console:
        def __init__(self):
            self.lines = []

        def print(self, value):
            self.lines.append(str(value))

    console = Console()
    install.run(SimpleNamespace(
        capture=str(_fixture(tmp_path / "capture")),
        name="receipt_contract",
        store_name="Receipt Contract",
    ), console)
    assert registered == [("receipt_contract", cells / "receipt_contract.db")]
    assert any("source bodies" in line and "1" in line for line in console.lines)


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


@pytest.mark.parametrize("missing", [
    "schema_version", "frontier_exhausted", "receipt_hash",
    "source_objects_hash", "spec_occurrences_hash", "relations_hash",
    "asset_inventory_hash",
])
def test_incomplete_capture_receipt_contract_fails_closed(tmp_path, missing):
    capture = _fixture(tmp_path / "capture")
    receipt_path = capture / "evidence/receipt.json"
    receipt = json.loads(receipt_path.read_text())
    if missing.endswith("_hash") and missing != "receipt_hash":
        artifact = missing.removesuffix("_hash")
        receipt["artifact_hashes"].pop(artifact)
        if artifact == "relations":
            relations_path = capture / "evidence/relations.json"
            relations = json.loads(relations_path.read_text())
            relations["rows"][0]["scope"] = "tampered-with-same-row-count"
            _dump(relations_path, relations)
    else:
        receipt.pop(missing)
    _dump(receipt_path, receipt)
    with pytest.raises(CaptureError):
        build_cell(capture, tmp_path / "catalogue.db")


def test_source_only_promotion_does_not_borrow_shared_content_properties(tmp_path):
    capture = _fixture(tmp_path / "capture")
    source_path = capture / "evidence/source_objects.json"
    payload = json.loads(source_path.read_text())
    descriptor = payload["rows"][0]["raw_descriptor"]
    named_url = "https://shop.test/brand/acme/named/sku-V1-NAMED"
    unnamed_url = "https://shop.test/brand/acme/unnamed/sku-V1-UNNAMED"
    payload["rows"].extend([
        {
            "source_id": "src_named_shared", "source_namespace": "xologic",
            "object_kind": "product_pdp", "identity_key": named_url,
            "requested_url": named_url, "fetch_url": named_url,
            "final_url": named_url, "status": 200,
            "parser_identity": "parser/v1", "config_identity": "config/v1",
            "code_identity": "code/v1", "raw_descriptor": descriptor,
            "structured_properties": [
                {"surface_ordinal": 1, "tag": "meta", "property": "schema:name",
                 "value": "Named Shared Source", "locator": "html:property[1]"},
            ],
        },
        {
            "source_id": "src_unnamed_shared", "source_namespace": "xologic",
            "object_kind": "product_pdp", "identity_key": unnamed_url,
            "requested_url": unnamed_url, "fetch_url": unnamed_url,
            "final_url": unnamed_url, "status": 200,
            "parser_identity": "parser/v1", "config_identity": "config/v1",
            "code_identity": "code/v1", "raw_descriptor": descriptor,
            "structured_properties": [],
        },
    ])
    _dump(source_path, payload)
    _reseal(capture)

    target = tmp_path / "catalogue.db"
    receipt = build_cell(capture, target)
    assert receipt["counts"]["products"] == 2
    with sqlite3.connect(target) as db:
        assert db.execute(
            "SELECT count(*) FROM products WHERE url=?", (named_url,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT count(*) FROM products WHERE url=?", (unnamed_url,)
        ).fetchone()[0] == 0


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
    assert receipt["counts"]["products"] == 2
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


def test_registered_cell_attaches_read_only_through_meta(tmp_path, monkeypatch):
    from flex import registry
    from flex.meta import attach_registered_cells

    flex_home = tmp_path / "flex-home"
    cells = flex_home / "cells"
    cells.mkdir(parents=True)
    monkeypatch.setattr(registry, "FLEX_HOME", flex_home)
    monkeypatch.setattr(registry, "CELLS_DIR", cells)
    monkeypatch.setattr(registry, "REGISTRY_DB", flex_home / "registry.db")

    capture = _fixture(tmp_path / "capture")
    target = cells / "test_xologic.db"
    build_cell(capture, target, store_name="Test")
    registry.register_cell(
        "test_xologic", target, cell_type="xologic",
        description="Test XOLogic", lifecycle="refresh",
        refresh_module="flex.modules.xologic.compile.refresh",
    )

    primary = sqlite3.connect(":memory:")
    remaining, error = attach_registered_cells(
        primary,
        "ATTACH 'test_xologic' AS catalogue; "
        "SELECT count(*) FROM catalogue.products",
        explicit_cells={"test_xologic"},
        available_cells={"test_xologic"},
    )
    assert error is None
    assert primary.execute(remaining).fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        primary.execute("DELETE FROM catalogue.products")
