from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sqlite3

import httpx
import pytest

from modules.woocommerce.compile.worker import (
    CaptureError,
    IntegrityError,
    build_cell_from_capture,
    build_staging_sqlite,
    fetch_store_api,
    load_capture_from_native_cell,
    verify_native_cell,
    verify_staging_sqlite,
)


SITE = "https://example-woo.test"


def _product(native_id: int) -> dict:
    return {
        "id": native_id,
        "slug": f"fixture-{native_id}",
        "name": f"Fixture {native_id} &#8211; 5000K",
        "permalink": f"{SITE}/product/fixture-{native_id}/",
        "type": "simple",
        "sku": f"SKU-{native_id}",
        "short_description": "<p>Short <strong>description</strong></p>",
        "description": "<p>Long fixture description</p>",
        "price_html": "$10.00",
        "average_rating": "4.8",
        "review_count": 4,
        "has_options": False,
        "is_in_stock": True,
        "is_on_backorder": False,
        "is_password_protected": False,
        "is_purchasable": True,
        "on_sale": native_id == 2,
        "sold_individually": False,
        "stock_availability": {"class": "in-stock", "text": "In stock"},
        "prices": {
            "price": "1000",
            "regular_price": "1200",
            "sale_price": "1000" if native_id == 2 else "",
            "price_range": {"min_amount": "1000", "max_amount": "1500"},
            "currency_code": "USD",
            "currency_symbol": "$",
            "currency_minor_unit": 2,
        },
        "attributes": [
            {
                "id": 10,
                "name": "Voltage",
                "taxonomy": "pa_voltage",
                "has_variations": False,
                "terms": [{"id": 20, "name": "120-277V", "slug": "120-277v"}],
            },
            {
                "id": 11,
                "name": "Custom field",
                "taxonomy": None,
                "has_variations": False,
                "value": "retained exactly",
                "terms": [],
            },
        ],
        "categories": [{"id": 30, "name": "High Bay", "slug": "high-bay"}],
        "tags": [{"id": 31, "name": "Warehouse", "slug": "warehouse"}],
        "brands": [{"id": 32, "name": "E2", "slug": "e2"}],
        "images": [
            {
                "id": 40,
                "src": f"{SITE}/media/{native_id}.webp",
                "thumbnail": f"{SITE}/media/{native_id}-thumb.webp",
                "srcset": "",
                "sizes": "100vw",
                "thumbnail_srcset": "",
                "thumbnail_sizes": "100vw",
                "name": f"Fixture {native_id}",
                "alt": "fixture photo",
            }
        ],
        "extensions": {"fixture_test": {"aisle": native_id, "hazmat": False}},
        "variations": [native_id * 10, native_id * 10 + 1],
    }


def _client(products: list[dict], *, total: int | None = None) -> httpx.Client:
    total = len(products) if total is None else total

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wp-json/wc/store/v1/products"
        params = dict(request.url.params)
        per_page = int(params["per_page"])
        page = int(params["page"])
        rows = products[(page - 1) * per_page:page * per_page]
        pages = (total + per_page - 1) // per_page
        return httpx.Response(
            200,
            json=rows,
            headers={
                "Content-Type": "application/json",
                "X-WP-Total": str(total),
                "X-WP-TotalPages": str(pages),
                "Set-Cookie": "do-not-persist-this-cookie=true",
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_raw_capture_staging_and_atomic_cell_preserve_store_population(tmp_path, monkeypatch):
    capture = fetch_store_api(SITE, client=_client([_product(1), _product(2), _product(3)]), per_page=2)
    assert capture.expected_count == 3
    assert capture.expected_pages == 2

    assert "set-cookie" not in capture.manifest["pages"][0]["headers"]

    staging = tmp_path / "raw.sqlite"
    staging_receipt = build_staging_sqlite(capture, staging)
    assert staging_receipt["captured_count"] == 3
    staging_sha_before = __import__("hashlib").sha256(staging.read_bytes()).hexdigest()
    assert verify_staging_sqlite(staging)["scalar_values"] > 0

    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "isolated-flex-home"))
    cell = tmp_path / "woocommerce.db"
    receipt = build_cell_from_capture(
        capture,
        cell,
        description="fixture preview",
    )
    assert receipt["atomic_replace"] is True
    assert staging_sha_before == __import__("hashlib").sha256(staging.read_bytes()).hexdigest()
    verified = verify_native_cell(cell)
    assert verified["catalogue_products"] == 3
    assert verified["search_documents"] == 3
    assert verified["fts_products"] == 3
    assert verified["product_dossiers"] == 3
    assert verified["dossier_payloads"] == 3

    with sqlite3.connect(cell) as db:
        assert db.execute("SELECT COUNT(*) FROM catalogue_products").fetchone()[0] == 3
        table_names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not table_names & {"_raw_chunks", "_raw_sources", "chunks_fts"}
        assert db.execute("SELECT COUNT(*) FROM woo_product_raw").fetchone()[0] == 3
        assert db.execute(
            "SELECT title FROM resolved_products WHERE product_ref='1'"
        ).fetchone()[0] == "Fixture 1 – 5000K"
        assert db.execute("SELECT COUNT(*) FROM catalogue_attributes WHERE attribute_name='Voltage'").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM catalogue_terms WHERE namespace='category'").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM catalogue_images").fetchone()[0] == 3
        assert db.execute(
            """SELECT value_integer FROM catalogue_json_values
                 WHERE product_id='2' AND source_path='$.extensions.fixture_test.aisle'"""
        ).fetchone()[0] == 2
        assert db.execute(
            """SELECT value_boolean FROM catalogue_json_values
                 WHERE product_id='2' AND source_path='$.extensions.fixture_test.hazmat'"""
        ).fetchone()[0] == 0
        raw = db.execute("SELECT raw_json FROM woo_product_raw WHERE native_id='1'").fetchone()[0]
        assert json.loads(raw)["extensions"]["fixture_test"]["aisle"] == 1
        orient = db.execute("SELECT sql FROM _presets WHERE name='orient'").fetchone()[0]
        assert "catalogue_json_values" in orient
        meta_keys = {row[0] for row in db.execute("SELECT key FROM _meta").fetchall()}
        assert {
            "cell_type", "embed", "description", "store_url", "scope",
            "provider", "profile", "authority",
        }.issubset(meta_keys)
        assert db.execute("SELECT value FROM _meta WHERE key='embed'").fetchone()[0] == "false"
        assert db.execute("SELECT value FROM _meta WHERE key='store_url'").fetchone()[0] == SITE
        assert db.execute("SELECT value FROM _meta WHERE key='profile'").fetchone()[0] == "woocommerce-lighting"
        assert "-- @query: examples" not in orient
        assert "-- @query: cell_docs" not in orient
        assert db.execute(
            "SELECT 1 FROM _presets WHERE name='self-orient'"
        ).fetchone() is None

    from flex.engine import execute_preset

    with sqlite3.connect(cell) as db:
        db.row_factory = sqlite3.Row
        oriented = json.loads(
            execute_preset(
                db,
                "@orient",
                materializer=lambda _db, sql: sql,
                cell_name="woocommerce_fixture",
            )
        )
    assert [row.get("query") for row in oriented] == [
        "identity", "shape", "relations", "boundaries"
    ]
    identity = next(row for row in oriented if row.get("query") == "identity")
    assert identity["results"][0]["profile"] == "woocommerce-lighting"
    relations = next(row for row in oriented if row.get("query") == "relations")
    assert "error" not in relations
    assert any(row["relation"] == "resolved_products" for row in relations["results"])
    assert any(row["relation"] == "product_dossiers" for row in relations["results"])


def test_capture_fails_closed_when_header_count_does_not_match_pages():
    client = _client([_product(1), _product(2)], total=3)
    with pytest.raises(IntegrityError, match="captured 2 products"):
        fetch_store_api(SITE, client=client, per_page=2)


def test_capture_fails_closed_on_duplicate_native_product_ids():
    client = _client([_product(1), _product(1)])
    with pytest.raises(IntegrityError, match="duplicate native product ids"):
        fetch_store_api(SITE, client=client, per_page=2)


def test_native_sqlite_rejects_changed_source_response_bytes(tmp_path):
    capture = fetch_store_api(SITE, client=_client([_product(1)]), per_page=1)
    database = tmp_path / "woocommerce.db"
    build_staging_sqlite(capture, database)
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE woo_native_document SET body=? WHERE kind='store_api_page'",
            (b"[]",),
        )
        db.commit()
    with pytest.raises(IntegrityError, match="page document hash differs"):
        verify_staging_sqlite(database)


def test_native_sqlite_rejects_page_bytes_rehashed_outside_sealed_manifest(tmp_path):
    capture = fetch_store_api(SITE, client=_client([_product(1)]), per_page=1)
    database = tmp_path / "woocommerce.db"
    build_staging_sqlite(capture, database)
    with sqlite3.connect(database) as db:
        original = db.execute(
            "SELECT body FROM woo_native_document WHERE kind='store_api_page'"
        ).fetchone()[0]
        changed = b" \n" + bytes(original) + b"\n"
        db.execute(
            """UPDATE woo_native_document SET body=?,body_sha256=?
                 WHERE kind='store_api_page'""",
            (changed, __import__("hashlib").sha256(changed).hexdigest()),
        )
        db.commit()
    with pytest.raises(IntegrityError, match="capture manifest differs"):
        verify_staging_sqlite(database)


def test_native_sqlite_rejects_changed_product_document_bytes(tmp_path):
    capture = fetch_store_api(SITE, client=_client([_product(1)]), per_page=1)
    database = tmp_path / "woocommerce.db"
    build_staging_sqlite(capture, database)
    with sqlite3.connect(database) as db:
        changed = b'{"id":1,"changed":true}'
        db.execute(
            "UPDATE woo_native_document SET body=?,body_sha256=? WHERE kind='store_api_product'",
            (changed, __import__("hashlib").sha256(changed).hexdigest()),
        )
        db.commit()
    with pytest.raises(IntegrityError, match="product document differs from raw JSON"):
        verify_staging_sqlite(database)


def test_retained_capture_can_rebuild_projection_without_network(tmp_path, monkeypatch):
    capture = fetch_store_api(SITE, client=_client([_product(1), _product(2)]), per_page=1)
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    cell = tmp_path / "retained.db"
    build_cell_from_capture(capture, cell)
    original_page_hashes = [page.sha256 for page in capture.pages]

    restored = load_capture_from_native_cell(cell)
    assert [page.sha256 for page in restored.pages] == original_page_hashes
    assert restored.manifest_sha256 == capture.manifest_sha256
    build_cell_from_capture(restored, cell)

    with sqlite3.connect(cell) as db:
        assert db.execute("SELECT COUNT(*) FROM resolved_products").fetchone()[0] == 2
        assert db.execute("SELECT manifest_sha256 FROM woo_capture").fetchone()[0] == capture.manifest_sha256


def test_legacy_spike_capture_identity_rebuilds_without_recapture(tmp_path, monkeypatch):
    capture = fetch_store_api(SITE, client=_client([_product(1)]), per_page=1)
    legacy = replace(capture, capture_format="woocommerce-native-raw-spike/v1")
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    cell = tmp_path / "legacy.db"
    build_cell_from_capture(legacy, cell)

    restored = load_capture_from_native_cell(cell)
    assert restored.capture_format == "woocommerce-native-raw-spike/v1"
    assert restored.capture_id == legacy.capture_id
    assert restored.manifest_sha256 == legacy.manifest_sha256


def test_login_required_price_policy_masks_query_views_but_preserves_raw_source(tmp_path, monkeypatch):
    capture = fetch_store_api(SITE, client=_client([_product(1)]), per_page=1)
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    cell = tmp_path / "login-pricing.db"
    build_cell_from_capture(capture, cell, price_policy="login_required")

    with sqlite3.connect(cell) as db:
        assert db.execute("SELECT price_raw FROM woo_product_price_raw").fetchone()[0] == "1000"
        assert db.execute(
            "SELECT price_visibility,price_raw,regular_price_raw,sale_price_raw FROM catalogue_products"
        ).fetchone() == ("login_required", None, None, None)
        assert db.execute(
            "SELECT price_visibility,price_raw,price FROM resolved_products"
        ).fetchone() == ("login_required", None, None)
        dossier = db.execute(
            "SELECT price_visibility,price,price_min,price_max FROM product_dossiers"
        ).fetchone()
        assert dossier == ("login_required", None, None, None)
        payload = json.loads(db.execute(
            "SELECT dossier_json FROM product_dossier_payloads"
        ).fetchone()[0])
        assert payload["price_visibility"] == "login_required"
        assert payload["price"] is None
        assert payload["price_min"] is None
        assert payload["price_max"] is None
        assert db.execute("SELECT value FROM _meta WHERE key='price_policy'").fetchone()[0] == "login_required"


def test_simple_product_price_is_its_range_fallback(tmp_path, monkeypatch):
    product = _product(90)
    product["prices"]["price_range"] = None
    capture = fetch_store_api(SITE, client=_client([product]), per_page=1)
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    cell = tmp_path / "simple-price.db"
    build_cell_from_capture(capture, cell)
    with sqlite3.connect(cell) as db:
        assert db.execute(
            "SELECT price,price_min,price_max FROM product_dossiers"
        ).fetchone() == (10.0, 10.0, 10.0)


def test_capture_requires_both_sealed_denominator_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_product(1)], headers={"X-WP-Total": "1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CaptureError, match="x-wp-totalpages"):
            fetch_store_api(SITE, client=client, per_page=1)


def test_commerce_projection_finds_literal_products_and_strict_source_backed_specs(tmp_path, monkeypatch):
    high_bay = _product(110)
    high_bay.update(
        name="110W Selectable Linear High Bay",
        permalink=f"{SITE}/product/110w-selectable-linear-high-bay/",
        short_description="<p>Power selectable 70W/90W/110W.</p>",
        description=(
            "<div>Selectable 4000K/5000K color temperature</div>"
            "<div>Input voltage: 277-480V</div>"
            "<div>0-10V dimming</div>"
            "<div>Finish Brown / Black / White Optional</div>"
            "<div>Dimmable YES</div>"
            "<div>DLC Listed YES</div>"
        ),
        attributes=[],
        images=[{
            "id": 41, "src": f"{SITE}/media/high-bay.webp", "thumbnail": "", "srcset": "",
            "sizes": "", "thumbnail_srcset": "", "thumbnail_sizes": "",
            "name": "Unrelated sibling product USB", "alt": "Unrelated sibling product USB",
        }],
    )
    usb_light = _product(111)
    usb_light.update(
        name="USB-Equipped LED Chart Light",
        permalink=f"{SITE}/product/usb-led-chart-light/",
        short_description="<p>Marine chart light with integrated USB charging port.</p>",
        attributes=[],
    )
    capture = fetch_store_api(SITE, client=_client([high_bay, usb_light]), per_page=2)
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    cell = tmp_path / "commerce.db"
    build_cell_from_capture(capture, cell, description="commerce regression")

    with sqlite3.connect(cell) as db:
        exact = db.execute(
            "SELECT title,product_url,wattage_min,wattage_max FROM resolved_products "
            "WHERE lower(searchable_text) LIKE '%110w%selectable%linear high bay%'"
        ).fetchone()
        assert exact == (
            "110W Selectable Linear High Bay",
            f"{SITE}/product/110w-selectable-linear-high-bay/",
            70.0,
            110.0,
        )
        strict = db.execute(
            """SELECT title,voltage_min,voltage_max,cct_min,cct_max,dimming_protocols
                 FROM resolved_products
                WHERE voltage_min<=480 AND voltage_max>=480
                  AND cct_min<=4000 AND cct_max>=5000
                  AND lower(COALESCE(dimming_protocols,'')) LIKE '%0-10v%'"""
        ).fetchall()
        assert strict == [("110W Selectable Linear High Bay", 277.0, 480.0, 4000.0, 5000.0, "0-10V")]
        assert db.execute(
            """SELECT DISTINCT is_selectable FROM resolved_spec_values
                 WHERE product_id='110' AND canonical_field='voltage'"""
        ).fetchall() == [(0,)]
        assert db.execute(
            "SELECT cct_selectable FROM resolved_products WHERE product_ref='110'"
        ).fetchone()[0] == 1
        evidence = db.execute(
            "SELECT source_path,label,value FROM spec_occurrences WHERE product_id='110' ORDER BY precedence,source_path"
        ).fetchall()
        assert any(path.startswith("$.description") and label == "Dimming" and value == "0-10V dimming"
                   for path, label, value in evidence)
        assert db.execute(
            """SELECT raw_value FROM resolved_spec_values
                 WHERE product_id='110' AND canonical_field='finish'"""
        ).fetchone()[0] == "Brown / Black / White Optional"
        assert db.execute(
            """SELECT raw_value FROM resolved_spec_values
                 WHERE product_id='110' AND canonical_field='safety_rating'"""
        ).fetchone()[0] == "DLC Listed YES"
        assert db.execute(
            "SELECT product_ref FROM products_fts WHERE products_fts MATCH 'black'"
        ).fetchall() == [("110",)]
        usb = db.execute(
            "SELECT title,product_url FROM resolved_products WHERE lower(searchable_text) LIKE '%usb%'"
        ).fetchall()
        assert usb == [("USB-Equipped LED Chart Light", f"{SITE}/product/usb-led-chart-light/")]
        orient = db.execute("SELECT sql FROM _presets WHERE name='orient'").fetchone()[0]
        assert "resolved_products" in orient
        assert "spec_occurrences" in orient
        assert "products_fts" in orient
        assert "product_dossiers" in orient
        assert "-- @query: examples" not in orient
        assert "chunks" not in orient.lower()


def test_search_identifiers_and_dossier_preserve_multivalued_options(tmp_path, monkeypatch):
    product = _product(501)
    product.update(
        name="Selectable DLC Premium High Bay",
        type="variable",
        has_options=True,
        is_in_stock=True,
        is_purchasable=False,
        variations=[
            {"id": 5011, "attributes": [{"attribute": "pa_finish", "value": "black"}]},
            {"id": 5012, "attributes": [{"attribute": "pa_finish", "value": "bronze"}]},
        ],
        attributes=[
            {
                "id": 21,
                "name": "Finish",
                "taxonomy": "pa_finish",
                "has_variations": True,
                "terms": [
                    {"id": 211, "name": "Black", "slug": "black"},
                    {"id": 212, "name": "Bronze", "slug": "bronze"},
                ],
            },
            {
                "id": 22,
                "name": "Certifications",
                "taxonomy": "pa_certifications",
                "has_variations": False,
                "terms": [
                    {"id": 221, "name": "UL", "slug": "ul"},
                    {"id": 222, "name": "DLC Premium", "slug": "dlc-premium"},
                ],
            },
        ],
    )
    capture = fetch_store_api(SITE, client=_client([product]), per_page=1)
    raw_sha256 = capture.pages[0].sha256
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    cell = tmp_path / "multivalue.db"
    build_cell_from_capture(capture, cell)

    with sqlite3.connect(cell) as db:
        assert db.execute(
            "SELECT product_ref FROM product_identifiers "
            "WHERE normalized_identifier='sku-501'"
        ).fetchall() == [("501",)]
        assert db.execute(
            "SELECT product_ref FROM products_fts WHERE products_fts MATCH 'DLC'"
        ).fetchall() == [("501",)]
        assert db.execute(
            "SELECT product_ref FROM products_fts WHERE products_fts MATCH 'high bay'"
        ).fetchall() == [("501",)]
        dossier_row = db.execute(
            "SELECT product_url,attributes_json,specifications_json,variations_json,raw_sha256,"
            "is_in_stock,is_purchasable "
            "FROM product_dossiers WHERE product_ref='501'"
        ).fetchone()
        assert dossier_row[0] == f"{SITE}/product/fixture-501/"
        attributes = json.loads(dossier_row[1])
        assert {row["value"] for row in attributes if row["name"] == "Finish"} == {"Black", "Bronze"}
        assert {row["value"] for row in attributes if row["name"] == "Certifications"} == {"UL", "DLC Premium"}
        specifications = json.loads(dossier_row[2])
        assert {row["value"] for row in specifications if row["canonical_field"] == "safety_rating"} == {
            "UL", "DLC Premium"
        }
        expected_variations = [
            {"id": 5011, "attributes": [{"attribute": "pa_finish", "value": "black"}]},
            {"id": 5012, "attributes": [{"attribute": "pa_finish", "value": "bronze"}]},
        ]
        assert json.loads(dossier_row[3]) == expected_variations
        assert dossier_row[5:] == (1, 0)
        payload = json.loads(db.execute(
            "SELECT dossier_json FROM product_dossier_payloads WHERE product_ref='501'"
        ).fetchone()[0])
        assert payload["variations"] == expected_variations
        assert len(payload["attributes"]) == 4
        page_body = db.execute(
            "SELECT body FROM woo_native_document WHERE kind='store_api_page'"
        ).fetchone()[0]
        assert __import__("hashlib").sha256(page_body).hexdigest() == raw_sha256

        assert verify_native_cell(cell)["product_dossiers"] == 1


def test_native_verifier_rejects_derived_coverage_drift(tmp_path, monkeypatch):
    capture = fetch_store_api(SITE, client=_client([_product(1), _product(2)]), per_page=2)
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    cell = tmp_path / "drift.db"
    build_cell_from_capture(capture, cell)
    with sqlite3.connect(cell) as db:
        db.execute("DELETE FROM product_search_documents WHERE product_ref='2'")
        db.commit()
    with pytest.raises(IntegrityError, match="search_documents coverage differs"):
        verify_native_cell(cell)


def test_authored_query_contract_has_fifteen_recipes_and_orient_has_none():
    module_root = Path(__file__).resolve().parents[1]
    profile_stock = module_root / "profiles" / "lighting" / "stock"
    instructions = (profile_stock / "instructions.md").read_text()
    orient = (profile_stock / "presets" / "orient.sql").read_text()

    assert sum(1 for line in instructions.splitlines() if line.startswith("## ") and line[3:4].isdigit()) == 15
    assert "product_identifiers" in instructions
    assert "products_fts" in instructions
    assert "product_dossiers" in instructions
    assert "product_dossier_payloads" in instructions
    assert "-- @query: examples" not in orient
    assert "-- @query: cell_docs" not in orient
    assert not (module_root / "stock" / "instructions.md").exists()
