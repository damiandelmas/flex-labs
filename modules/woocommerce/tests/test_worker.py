from __future__ import annotations

import json
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
    assert verify_native_cell(cell)["catalogue_products"] == 3

    with sqlite3.connect(cell) as db:
        assert db.execute("SELECT COUNT(*) FROM catalogue_products").fetchone()[0] == 3
        table_names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not table_names & {"_raw_chunks", "_raw_sources", "chunks_fts"}
        assert db.execute("SELECT COUNT(*) FROM woo_product_raw").fetchone()[0] == 3
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
        self_orient = db.execute("SELECT sql FROM _presets WHERE name='self-orient'").fetchone()[0]
        orient = db.execute("SELECT sql FROM _presets WHERE name='orient'").fetchone()[0]
        assert "catalogue_json_values" in self_orient
        assert "catalogue_json_values" in orient
        meta_keys = {row[0] for row in db.execute("SELECT key FROM _meta").fetchall()}
        assert {"cell_type", "description", "store_url", "scope"}.issubset(meta_keys)
        assert db.execute("SELECT value FROM _meta WHERE key='store_url'").fetchone()[0] == SITE

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
    assert any(row.get("query") == "capture" for row in oriented)
    assert any(row.get("query") == "shape" for row in oriented)
    columns = next(row for row in oriented if row.get("query") == "columns")
    assert any(row["surface"] == "catalogue_products" for row in columns["results"])
    docs = next(row for row in oriented if row.get("query") == "cell_docs")
    assert "Exact page bytes and product JSON are authoritative" in docs["results"][0]["content"]
    query_surface = next(row for row in oriented if row.get("query") == "query_surface")
    assert "error" not in query_surface
    assert any(row["name"] == "catalogue_products" for row in query_surface["results"])


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


def test_capture_requires_both_sealed_denominator_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_product(1)], headers={"X-WP-Total": "1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CaptureError, match="x-wp-totalpages"):
            fetch_store_api(SITE, client=client, per_page=1)
