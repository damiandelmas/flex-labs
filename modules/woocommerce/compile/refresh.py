"""Atomic refresh for WooCommerce Store API Flex cells."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from flex.modules.woocommerce.compile.worker import (
    DEFAULT_REFRESH_INTERVAL,
    PRODUCTS_PATH,
    build_cell_from_store_api,
    fetch_store_api,
    resolve_cell_path,
)


def _meta(cell_path: Path) -> dict[str, str]:
    with sqlite3.connect(f"file:{cell_path.resolve()}?mode=ro", uri=True) as db:
        return dict(db.execute("SELECT key,value FROM _meta").fetchall())


def refresh(
    cell_path: str,
    *,
    store_url: str | None = None,
    per_page: int | None = None,
    retries: int = 2,
    dry_run: bool = False,
) -> dict:
    """Pull one complete Store API population and atomically replace the cell."""
    target = resolve_cell_path(cell_path)
    meta = _meta(target) if target.exists() else {}
    resolved_store_url = (store_url or meta.get("store_url") or "").rstrip("/")
    if not resolved_store_url:
        raise ValueError("store_url is required for first refresh")
    resolved_per_page = per_page or int(meta.get("per_page") or "50")

    if dry_run:
        capture = fetch_store_api(resolved_store_url, per_page=resolved_per_page, retries=retries)
        return {
            "dry_run": True,
            "store_url": resolved_store_url,
            "endpoint": resolved_store_url + PRODUCTS_PATH,
            "expected_count": capture.expected_count,
            "expected_pages": capture.expected_pages,
            "manifest_sha256": capture.manifest_sha256,
        }

    return build_cell_from_store_api(
        resolved_store_url,
        target,
        description=meta.get("description") or "Raw WooCommerce Store API catalogue",
        price_policy=meta.get("price_policy") or "public",
        per_page=resolved_per_page,
        retries=retries,
        refresh_interval=int(meta.get("refresh_interval") or DEFAULT_REFRESH_INTERVAL),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", default="woocommerce", help="Cell name or explicit SQLite path")
    parser.add_argument("--store-url", "--url", dest="store_url", default=None)
    parser.add_argument("--per-page", type=int, default=None)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    receipt = refresh(
        args.cell,
        store_url=args.store_url,
        per_page=args.per_page,
        retries=args.retries,
        dry_run=args.dry_run,
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
