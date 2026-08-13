#!/usr/bin/env python3
"""Validate the authored XOLogic retrieval recipes against one SQLite artifact."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .worker import _canonical, _digest_file, _sha256_uri, _write_json_atomic


VALIDATION_SCHEMA = "xologic-recipe-validation/v1"
INSTRUCTIONS_PATH = Path(__file__).resolve().parent.parent / "stock" / "instructions.md"
EXPECTED_SQL_SHA256 = "d65b8a5157e8b22071089346210ac4b16e9176ad41fdcb56a244bcd6dafef0d6"
REQUIRED_PRODUCT_COLUMNS = {
    "total_matches", "returned", "has_more", "product_ref", "url", "sku",
    "title", "specifications_json",
}

# Exact baseline from session 019ff9cc-228b-7b70-ab3c-a92481d8060a,
# message 2879, plus the first SKU captured by the same validation run.
BES_BASELINE = (
    (5, 5, False, 26213, "43999BK"),
    (492, 5, True, 12744, "10264 BK"),
    (69, 5, True, 21820, "12724FB3"),
    (47, 5, True, 21637, "HF1170-BK"),
    (1, 1, False, 4300, "12430SB4"),
    (1, 1, False, 5536, "OL13701TXB-L1"),
    (70, 5, True, 25228, "8-8888-18-BK"),
    (66, 5, True, 23003, "AC12150BB"),
    (2, 2, False, 8872, "12430SB4"),
    (5, 5, False, 26213, "43999BK"),
    (3, 3, False, 15653, "43999BK"),
    (28, 5, True, 20570, "55902CRBK"),
    (2, 2, False, 8872, "50504-SB"),
    (26, 5, True, 20615, "LCH288A16BK"),
    (25, 5, True, 17297, "539-2-59"),
)


class RecipeContractError(RuntimeError):
    pass


def load_recipes(path: Path = INSTRUCTIONS_PATH) -> tuple[list[str], str]:
    text = Path(path).read_text()
    headings = [
        int(value) for value in re.findall(r"^## ([0-9]+)\.", text, re.MULTILINE)
    ]
    recipes = re.findall(r"^```sql\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
    if headings != list(range(1, 16)) or len(recipes) != 15:
        raise RecipeContractError(
            "XOLogic instructions must contain headings 1..15 and exactly fifteen SQL fences"
        )
    digest = sha256("\n\n".join(recipes).encode("utf-8")).hexdigest()
    if digest != EXPECTED_SQL_SHA256:
        raise RecipeContractError(
            f"XOLogic recipe SQL changed: expected {EXPECTED_SQL_SHA256}, got {digest}"
        )
    return recipes, digest


def _bes_bindings(db: sqlite3.Connection) -> list[dict[str, Any]]:
    identifier_refs = [
        row["product_ref"] for row in db.execute("""
          SELECT DISTINCT i.product_ref
          FROM product_identifiers i
          JOIN resolved_products p USING(product_ref)
          WHERE i.normalized_identifier LIKE '43999%'
          ORDER BY p.sku,p.product_ref
        """)
    ]
    shown_refs = [
        row["product_ref"] for row in db.execute("""
          SELECT p.product_ref
          FROM resolved_products p
          WHERE p.price_min<=200
            AND lower(COALESCE(p.finish,'')) LIKE '%black%'
            AND (
              lower(COALESCE(p.product_type,'')) LIKE '%vanity%'
              OR EXISTS (
                SELECT 1 FROM taxonomy_paths t
                WHERE t.product_ref=p.product_ref
                  AND lower(t.label) LIKE '%bathroom%'
              )
            )
          ORDER BY p.price_min,p.title,p.product_ref
          LIMIT 2
        """)
    ]
    if len(identifier_refs) < 3 or len(shown_refs) < 2:
        raise RecipeContractError(
            "BES validation bindings cannot be derived from the built database"
        )
    return [
        {"identifier": "43999"},
        {"terms": "pendant"},
        {"terms": "vanity black", "max_price": 200},
        {"terms": "sconce", "max_price": 300, "excluded_vendor": "Nuvo"},
        {"terms": "vanity brass seeded"},
        {
            "terms": "outdoor wall black", "max_price": 300,
            "minimum_lumens": 1000, "required_voltage": 120,
        },
        {"max_price": 200},
        {"max_price": 600},
        {"allow_transitional": 1},
        {"product_ref": identifier_refs[0]},
        {"product_refs_json": json.dumps(identifier_refs[:3])},
        {},
        {
            "title_terms": "title:vanity",
            "specification_terms": "specifications:brass AND specifications:seeded",
        },
        {"max_price": 500},
        {
            "shown_product_refs_json": json.dumps(shown_refs),
            "original_terms": "vanity black",
            "new_price_ceiling": 150,
            "required_finish": "black",
            "required_type": "vanity",
            "required_style": None,
            "excluded_vendor": "Nuvo",
        },
    ]


def _validate_one(db: sqlite3.Connection, number: int, sql: str,
                  bindings: dict[str, Any]) -> dict[str, Any]:
    try:
        cursor = db.execute(sql, bindings)
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        missing = sorted(REQUIRED_PRODUCT_COLUMNS - set(columns))
        total = int(rows[0]["total_matches"]) if rows else 0
        returned = int(rows[0]["returned"]) if rows else 0
        has_more = bool(rows[0]["has_more"]) if rows else False
        metadata_consistent = all(
            int(row["total_matches"]) == total
            and int(row["returned"]) == returned
            and bool(row["has_more"]) == has_more
            for row in rows
        )
        product_refs = [row["product_ref"] for row in rows]
        payload_chars = sum(
            int(db.execute(
                "SELECT length(dossier_json) FROM product_dossier_payloads "
                "WHERE product_ref=?", (product_ref,),
            ).fetchone()[0])
            for product_ref in product_refs
        )
        first_sku = rows[0]["sku"] if rows else None
        expected = BES_BASELINE[number - 1]
        observed = (total, returned, has_more, payload_chars, first_sku)
        structural_ok = (
            not missing
            and len(rows) <= 5
            and returned == len(rows)
            and has_more == (total > returned)
            and metadata_consistent
            and (len(rows) <= 1 or payload_chars <= 30000)
        )
        return {
            "query": number,
            "ok": structural_ok and observed == expected,
            "total": total,
            "returned": returned,
            "has_more": has_more,
            "payload_chars": payload_chars,
            "first_sku": first_sku,
            "missing": missing,
            "expected": {
                "total": expected[0], "returned": expected[1],
                "has_more": expected[2], "payload_chars": expected[3],
                "first_sku": expected[4],
            },
        }
    except Exception as error:  # The receipt must retain the exact failed query boundary.
        return {
            "query": number,
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
        }


def validate_bes_recipes(cell_path: Path, *, build_receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    target = Path(cell_path).resolve()
    recipes, sql_digest = load_recipes()
    cell_sha256 = _sha256_uri(_digest_file(target))
    build_identity = None
    if build_receipt is not None:
        expected_cell = (build_receipt.get("output") or {}).get("cell_sha256")
        if expected_cell != cell_sha256:
            raise RecipeContractError(
                "build receipt does not identify the database under validation"
            )
        build_identity = build_receipt.get("content_identity_sha256")
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        bindings = _bes_bindings(db)
        results = [
            _validate_one(db, number, sql, params)
            for number, (sql, params) in enumerate(zip(recipes, bindings), 1)
        ]
    passed = sum(result["ok"] is True for result in results)
    receipt: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA,
        "locations": {"cell_path": str(target)},
        "profile": "bes",
        "build_content_identity_sha256": build_identity,
        "cell_sha256": cell_sha256,
        "instructions_sha256": _sha256_uri(_digest_file(INSTRUCTIONS_PATH)),
        "extracted_sql_sha256": _sha256_uri(sql_digest),
        "query_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    identity = {key: value for key, value in receipt.items() if key != "locations"}
    receipt["content_identity_sha256"] = _sha256_uri(
        sha256(_canonical(identity).encode("utf-8")).hexdigest()
    )
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--build-receipt")
    parser.add_argument("--receipt")
    args = parser.parse_args(argv)
    if args.receipt and Path(args.receipt).resolve() == Path(args.cell).resolve():
        raise SystemExit("validation receipt path must differ from the SQLite artifact path")
    build_receipt = (
        json.loads(Path(args.build_receipt).read_text())
        if args.build_receipt else None
    )
    receipt = validate_bes_recipes(Path(args.cell), build_receipt=build_receipt)
    if args.receipt:
        _write_json_atomic(Path(args.receipt), receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
