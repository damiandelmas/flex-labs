from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

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


def _reseal(root: Path, schema_version: str = "xologic-lossless/v1") -> None:
    artifacts = ("source_objects", "spec_occurrences", "relations", "asset_inventory")
    counts = {}
    hashes = {}
    for name in artifacts:
        path = root / "evidence" / f"{name}.json"
        rows = json.loads(path.read_text())["rows"]
        counts[name] = len(rows)
        hashes[name] = "sha256:" + _sha(path)
    _dump(root / "evidence/receipt.json", {
        "schema_version": schema_version,
        "sealed": True, "complete": True, "frontier_exhausted": True,
        "receipt_hash": "sha256:fixture-resealed",
        "artifact_hashes": hashes, "artifact_counts": counts,
    })


def _fixture_v2(root: Path) -> Path:
    capture = _fixture(root)
    products_path = capture / "structured/products.json"
    products = json.loads(products_path.read_text())
    product = products["products"][0]
    product.update({
        "source_id": "src_base",
        "requested_url": product["url"],
        "product_key": "acme:abc",
        "source_aliases": [
            {"source_id": "src_base", "requested_url": product["url"],
             "final_url": product["url"]},
            {"source_id": "src_fragment",
             "requested_url": product["url"] + "#itemDetailFinish",
             "final_url": product["url"]},
        ],
    })
    _dump(products_path, products)

    source_path = capture / "evidence/source_objects.json"
    sources = json.loads(source_path.read_text())
    for source in sources["rows"]:
        source.update({
            "redirects": [], "provider_keys": {"product_key": "acme:abc"},
            "description": "Fixture description",
        })
    _dump(source_path, sources)

    occurrence_path = capture / "evidence/spec_occurrences.json"
    occurrences = json.loads(occurrence_path.read_text())
    for ordinal, row in enumerate(occurrences["rows"], 1):
        row.update({
            "occurrence_id": f"occ_{ordinal:04d}",
            "source_namespace": "xologic", "object_kind": "product_pdp",
            "section_heading": (
                "Product Specification" if row["clean_label"] == "Item #"
                else "Additional product details"
            ),
            "row_attributes": {"class": row["row_classes"]},
            "row_start_tag": '<div class="dataDetailRow">',
            "label_attributes": {"class": "dataColLeft"},
            "label_start_tag": '<div class="dataColLeft">',
            "value_attributes": {"class": "dataColRight"},
            "value_start_tag": '<div class="dataColRight">',
        })
        if row["clean_label"] == "Item #":
            row["source_layer"] = "product_specification"
    occurrences["rows"].append({
        "occurrence_id": "occ_other_details", "source_id": "src_base",
        "source_namespace": "xologic", "object_kind": "product_pdp",
        "source_layer": "other_details", "section_heading": "Other Details",
        "source_order": 15, "surface_ordinal": 15,
        "clean_label": "Other Details", "clean_value": "Handcrafted",
        "raw_label": "Other Details", "raw_value": "Handcrafted",
        "locator": "html:dataDetailRow[15]", "row_classes": "",
        "row_attributes": {}, "row_start_tag": "<li>",
        "label_attributes": {}, "label_start_tag": "",
        "value_attributes": {}, "value_start_tag": "",
        "visibility_evidence": {"hidden": False},
    })
    _dump(occurrence_path, occurrences)

    base = product["url"]
    relation_rows = [
        ("rel_option", "src_base", "option_for", base + "#itemDetailFinish",
         "Additional options that would change other selections", "Black"),
        ("rel_download", "src_fragment", "document_for",
         "https://shop.test/files/install.pdf", "Downloads", "Installation Guide"),
        ("rel_image", "src_base", "image_for",
         "https://shop.test/images/abc.jpg", None, "Fixture image"),
        ("rel_collection", "src_base", "product_link", base + "-collection",
         "More In This Collection", "Collection fixture"),
        ("rel_related", "src_base", "product_link", base + "-related",
         "Related Products", "Related fixture"),
    ]
    relations = []
    for ordinal, (relation_id, source_id, kind, target, section, label) in enumerate(
        relation_rows, 1
    ):
        relations.append({
            "relation_id": relation_id, "source_id": source_id,
            "source_namespace": "xologic", "source_object_kind": "product_pdp",
            "kind": kind, "target_url": target, "target_id": "target_" + relation_id,
            "target_namespace": "xologic",
            "target_object_kind": (
                "document" if kind == "document_for" else
                "image" if kind == "image_for" else "product_pdp"
            ),
            "target_source_id": (
                "target_" + relation_id
                if kind in {"option_for", "product_link"} else None
            ),
            "locator": f"html:link[{ordinal}]", "raw_href": target,
            "surface_ordinal": ordinal, "tag": "a",
            "attributes": {"href": target}, "raw_start_tag": f'<a href="{target}">',
            "visible_text": label, "section_heading": section,
            "fragment_meaningful": "#" in target,
            "scope": (
                "discover_only" if kind in {"document_for", "image_for"}
                else "source_relation"
            ),
        })
    _dump(capture / "evidence/relations.json", {
        "artifact": "relations", "version": 2, "rows": relations,
    })
    assets = [
        {"asset_id": "asset_document", "object_kind": "document",
         "url": "https://shop.test/files/install.pdf", "source_ids": ["src_fragment"],
         "locators": ["html:link[2]"], "scope": "discover_only",
         "raw_href": "https://shop.test/files/install.pdf", "surface_ordinal": 2,
         "tag": "a", "attributes": {"href": "https://shop.test/files/install.pdf"},
         "raw_start_tag": '<a href="https://shop.test/files/install.pdf">',
         "visible_text": "Installation Guide", "section_heading": "Downloads"},
        {"asset_id": "asset_image", "object_kind": "image",
         "url": "https://shop.test/images/abc.jpg", "source_ids": ["src_base"],
         "locators": ["html:link[3]"], "scope": "discover_only",
         "raw_href": "https://shop.test/images/abc.jpg", "surface_ordinal": 3,
         "tag": "img", "attributes": {"src": "https://shop.test/images/abc.jpg"},
         "raw_start_tag": '<img src="https://shop.test/images/abc.jpg">',
         "visible_text": "Fixture image", "section_heading": None},
    ]
    _dump(capture / "evidence/asset_inventory.json", {
        "artifact": "asset_inventory", "version": 2, "rows": assets,
    })
    _dump(capture / "receipt.json", {
        "complete": True, "discovery_complete": True, "enumeration": "public_graph",
    })
    _reseal(capture, "xologic-lossless/v2")
    return capture


def test_native_cell_preserves_source_observations_and_relations(tmp_path, monkeypatch):
    capture = _fixture(tmp_path / "capture")
    target = tmp_path / "bes_lighting_xologic.db"
    receipt = build_cell(capture, target, store_name="BES Lighting")
    counts = receipt["counts"]
    assert counts["products"] == 1
    assert counts["xologic_source_observations"] == 2
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
        source_rows = db.execute(
            "SELECT content_hash,descriptor_path FROM xologic_source_observations "
            "ORDER BY source_id"
        ).fetchall()
        assert len({row[0] for row in source_rows}) == 1
        assert len({row[1] for row in source_rows}) == 1
        assert sha256((capture / source_rows[0][1]).read_bytes()).hexdigest() == source_rows[0][0]
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
        required = {
            ("table", "products"), ("table", "xologic_editions"),
            ("table", "xologic_source_observations"),
            ("table", "source_properties"), ("table", "spec_occurrences"),
            ("table", "product_links"), ("table", "product_resources"),
            ("table", "products_fts"), ("view", "product_dossiers"),
            ("view", "product_specification"),
            ("view", "additional_product_details"),
            ("view", "other_details"), ("view", "images"),
            ("view", "downloads"), ("view", "options"),
            ("view", "additional_options_that_would_change_other_selections"),
            ("view", "collection_products"), ("view", "related_products"),
        }
        assert required <= objects
        assert not {"xologic_content_objects", "_raw_chunks", "_raw_sources"} & {
            name for _, name in objects
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
    assert {row.get("query") for row in result} == {
        "identity", "capabilities", "edition", "shape", "query_surface",
        "limitations",
    }
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


def test_v2_evidence_is_preserved_as_source_shaped_sql(tmp_path):
    capture = _fixture_v2(tmp_path / "capture")
    target = tmp_path / "catalogue.db"
    receipt = build_cell(capture, target, store_name="Source Surface")

    assert receipt["counts"]["spec_occurrences"] == 29
    assert receipt["counts"]["product_links"] == 5
    assert receipt["counts"]["product_resources"] == 2

    with sqlite3.connect(target) as db:
        db.row_factory = sqlite3.Row
        assert {row[0] for row in db.execute(
            "SELECT occurrence_id FROM spec_occurrences"
        )} == {
            *(f"occ_{ordinal:04d}" for ordinal in range(1, 29)),
            "occ_other_details",
        }
        assert {row[0] for row in db.execute(
            "SELECT link_id FROM product_links"
        )} == {
            "rel_option", "rel_download", "rel_image", "rel_collection",
            "rel_related",
        }
        specification = db.execute(
            "SELECT label,value FROM product_specification"
        ).fetchall()
        assert [tuple(row) for row in specification] == [
            ("Item #", "ABC"),
            ("Item #", "ABC"),
        ]
        assert db.execute(
            "SELECT count(*) FROM additional_product_details"
        ).fetchone()[0] == 26
        assert tuple(db.execute(
            "SELECT label,value,raw_value FROM other_details"
        ).fetchone()) == ("Other Details", "Handcrafted", "Handcrafted")
        assert tuple(db.execute(
            "SELECT visible_text,url,section_heading FROM downloads"
        ).fetchone()) == (
            "Installation Guide", "https://shop.test/files/install.pdf", "Downloads",
        )
        assert tuple(db.execute(
            "SELECT visible_text,fragment_meaningful FROM options"
        ).fetchone()) == ("Black", 1)
        assert db.execute(
            "SELECT count(*) FROM additional_options_that_would_change_other_selections"
        ).fetchone()[0] == 1
        assert db.execute("SELECT visible_text FROM collection_products").fetchone()[0] == (
            "Collection fixture"
        )
        assert db.execute("SELECT visible_text FROM related_products").fetchone()[0] == (
            "Related fixture"
        )
        assert db.execute("SELECT visible_text FROM images").fetchone()[0] == (
            "Fixture image"
        )
        metadata = dict(db.execute("SELECT key,value FROM _metadata"))
        assert metadata["evidence_schema_version"] == "xologic-lossless/v2"


def test_v2_dossier_v3_payload_preserves_relation_parity_and_boundaries(tmp_path):
    capture = _fixture_v2(tmp_path / "capture")
    target = tmp_path / "catalogue.db"
    build_cell(capture, target, store_name="Dossier V3")

    relation_columns = {
        "images": (
            "relation_id", "source_id", "surface_ordinal", "visible_text",
            "url", "raw_href", "locator", "tag", "section_heading", "scope",
        ),
        "downloads": (
            "relation_id", "source_id", "surface_ordinal", "visible_text",
            "url", "raw_href", "locator", "tag", "section_heading", "scope",
        ),
        "options": (
            "relation_id", "source_id", "surface_ordinal", "visible_text",
            "url", "raw_href", "locator", "tag", "section_heading",
            "fragment_meaningful", "target_id", "target_source_id",
        ),
        "additional_options_that_would_change_other_selections": (
            "relation_id", "source_id", "surface_ordinal", "visible_text",
            "url", "raw_href", "locator", "tag", "section_heading",
            "fragment_meaningful", "target_id", "target_source_id",
        ),
        "collection_products": (
            "relation_id", "source_id", "surface_ordinal", "visible_text",
            "url", "raw_href", "locator", "tag", "target_id",
            "target_source_id",
        ),
        "related_products": (
            "relation_id", "source_id", "surface_ordinal", "visible_text",
            "url", "raw_href", "locator", "tag", "target_id",
            "target_source_id",
        ),
    }

    with sqlite3.connect(target) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM product_dossiers").fetchone()
        assert row["details_status"] == "live"
        assert row["terminal_observation_count"] == 2
        assert row["terminal_provider_count"] == 1
        assert row["discovery_observation_count"] == 2
        assert row["listing_price_observation_count"] == 1
        assert row["listing_price_distinct_count"] == 1
        assert json.loads(row["terminal_providers"]) == [{
            "source_namespace": "xologic",
            "object_kind": "product_pdp",
            "parser_identity": "parser/v1",
            "config_identity": "config/v1",
            "code_identity": "code/v1",
            "observation_count": 2,
        }]
        completeness = json.loads(row["source_completeness_json"])
        assert completeness["expected_source_properties"] == 2
        assert completeness["expected_spec_occurrences"] == 29
        assert completeness["expected_product_links"] == 5
        assert completeness["expected_product_resources"] == 2
        assert completeness["source_properties"] == "present"
        assert completeness["serving_projection_is_source_claim"] is False

        for relation, columns in relation_columns.items():
            native = [
                {column: value for column, value in zip(columns, values)}
                for values in db.execute(
                    f"SELECT {','.join(columns)} FROM {relation} "
                    "ORDER BY COALESCE(surface_ordinal,0),url,relation_id"
                )
            ]
            assert json.loads(row[f"{relation}_json"]) == native

        assert json.loads(row["images_json"])[0]["relation_id"] == "rel_image"
        assert json.loads(row["downloads_json"])[0]["relation_id"] == "rel_download"
        assert json.loads(row["options_json"])[0]["relation_id"] == "rel_option"
        payload = json.loads(db.execute(
            "SELECT dossier_json FROM product_dossier_payloads"
        ).fetchone()[0])
        assert payload["details_status"] == "live"
        assert payload["terminal"]["observation_count"] == 2
        assert payload["terminal"]["providers"] == json.loads(row["terminal_providers"])
        assert payload["discovery"] == {"observation_count": 2}
        assert payload["source_completeness"] == completeness
        assert payload["images"] == json.loads(row["images_json"])
        assert payload["downloads"] == json.loads(row["downloads_json"])
        assert payload["options"] == json.loads(row["options_json"])
        assert payload["additional_options_that_would_change_other_selections"] == (
            json.loads(row["additional_options_that_would_change_other_selections_json"])
        )
        assert payload["collection_products"] == json.loads(row["collection_products_json"])
        assert payload["related_products"] == json.loads(row["related_products_json"])


def test_dossier_v3_marks_terminal_details_stale_without_live_observation(tmp_path):
    capture = _fixture_v2(tmp_path / "capture")
    source_path = capture / "evidence/source_objects.json"
    payload = json.loads(source_path.read_text())
    for source in payload["rows"]:
        source["status"] = 404
    _dump(source_path, payload)
    _reseal(capture, "xologic-lossless/v2")

    target = tmp_path / "catalogue.db"
    build_cell(capture, target)
    with sqlite3.connect(target) as db:
        assert db.execute(
            "SELECT details_status,terminal_observation_count "
            "FROM product_dossiers"
        ).fetchone() == ("stale", 2)
        decoded = json.loads(db.execute(
            "SELECT dossier_json FROM product_dossier_payloads"
        ).fetchone()[0])
        assert decoded["details_status"] == "stale"


def test_dossier_v3_is_honest_when_source_properties_are_empty(tmp_path):
    capture = _fixture_v2(tmp_path / "capture")
    source_path = capture / "evidence/source_objects.json"
    payload = json.loads(source_path.read_text())
    for source in payload["rows"]:
        source["structured_properties"] = []
    _dump(source_path, payload)
    _reseal(capture, "xologic-lossless/v2")

    target = tmp_path / "catalogue.db"
    build_cell(capture, target)
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT count(*) FROM source_properties").fetchone()[0] == 0
        completeness = json.loads(db.execute(
            "SELECT source_completeness_json FROM product_dossiers"
        ).fetchone()[0])
        assert completeness["source_properties"] == "absent_or_empty"
        assert completeness["expected_source_properties"] == 0
        assert completeness["serving_projection_is_source_claim"] is False
        assert db.execute("SELECT count(*) FROM product_links").fetchone()[0] == 5


def test_v2_duplicate_evidence_identity_fails_closed(tmp_path):
    capture = _fixture_v2(tmp_path / "capture")
    path = capture / "evidence/spec_occurrences.json"
    payload = json.loads(path.read_text())
    payload["rows"][1]["occurrence_id"] = payload["rows"][0]["occurrence_id"]
    _dump(path, payload)
    _reseal(capture, "xologic-lossless/v2")
    with pytest.raises(CaptureError, match="duplicate occurrence_id"):
        build_cell(capture, tmp_path / "catalogue.db")


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
        "cell_sha256", "edition_id", "orient_sha256", "instructions_sha256",
        "counts",
    }
    assert receipt["schema_version"] == "xologic-native-flex-build/v3"
    assert receipt["capture_receipt"] == "sha256:fixture"
    assert receipt["transformation_sha256"].startswith("sha256:")
    assert receipt["cell_sha256"] == "sha256:" + _sha(target)
    assert receipt["orient_sha256"].startswith("sha256:")
    assert receipt["instructions_sha256"].startswith("sha256:")
    assert receipt["counts"]["products"] == 1
    with pytest.raises(CaptureError, match="must differ"):
        build_cell(capture, target, receipt_path=target)


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
    assert any("source observations" in line and "2" in line for line in console.lines)


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
