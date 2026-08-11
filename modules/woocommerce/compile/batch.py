#!/usr/bin/env python3
"""Bulk WooCommerce Store API cell builder.

The batch runner is deliberately only a controlled wrapper around the native
single-store worker. Each manifest row builds one source-faithful SQLite cell
with ``build_cell_from_store_api`` and then registers that exact path with
``register_cell_path``.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:  # pragma: no cover - supports direct script execution.
    from .worker import (
        DEFAULT_REFRESH_INTERVAL,
        CaptureError,
        IntegrityError,
        build_cell_from_store_api,
        register_cell_path,
        resolve_cell_path,
    )
except ImportError:  # pragma: no cover
    from modules.woocommerce.compile.worker import (
        DEFAULT_REFRESH_INTERVAL,
        CaptureError,
        IntegrityError,
        build_cell_from_store_api,
        register_cell_path,
        resolve_cell_path,
    )


@dataclass(frozen=True)
class BatchRow:
    index: int
    cell: str
    url: str
    description: str | None = None


def _load_manifest(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("manifest must be a JSON array")
    if not payload:
        raise ValueError("manifest must contain at least one row")
    if not all(isinstance(row, dict) for row in payload):
        raise ValueError("manifest rows must be JSON objects")
    return payload


def _normalize_url(url: str) -> str:
    value = url.strip().rstrip("/")
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"manifest url must be absolute: {url!r}")
    return value


def _url_key(url: str) -> tuple[str, str, str, str]:
    parsed = urlsplit(url)
    return (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query)


def _description(value: Any, *, index: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest row {index} description must be a non-empty string when present")
    return value


def validate_manifest_rows(rows: list[dict[str, Any]]) -> list[BatchRow]:
    """Validate and normalize manifest rows before any source build starts."""
    parsed: list[BatchRow] = []
    for index, row in enumerate(rows):
        allowed = {"cell", "url", "description"}
        extra = sorted(set(row) - allowed)
        if extra:
            raise ValueError(f"manifest row {index} has unsupported keys: {extra}")
        cell = row.get("cell")
        url = row.get("url")
        if not isinstance(cell, str) or not cell.strip():
            raise ValueError(f"manifest row {index} cell must be a non-empty string")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"manifest row {index} url must be a non-empty string")
        parsed.append(
            BatchRow(
                index=index,
                cell=cell.strip(),
                url=_normalize_url(url),
                description=_description(row.get("description"), index=index),
            )
        )

    cells: dict[str, int] = {}
    urls: dict[str, int] = {}
    for row in parsed:
        cell_path = str(resolve_cell_path(row.cell).expanduser().resolve())
        if cell_path in cells:
            raise ValueError(
                f"duplicate manifest cell at rows {cells[cell_path]} and {row.index}: {row.cell}"
            )
        cells[cell_path] = row.index
        url_key = _url_key(row.url)
        if url_key in urls:
            raise ValueError(
                f"duplicate manifest url at rows {urls[url_key]} and {row.index}: {row.url}"
            )
        urls[url_key] = row.index
    return parsed


def _cell_name(cell: str) -> str:
    path = Path(cell)
    return path.stem if path.suffix in {".db", ".sqlite", ".sqlite3"} else cell


def _build_one(
    row: BatchRow,
    *,
    per_page: int,
    retries: int,
    retry_delay: float,
    refresh_interval: int,
) -> dict[str, Any]:
    cell_path = resolve_cell_path(row.cell)
    receipt = build_cell_from_store_api(
        row.url,
        cell_path,
        description=row.description,
        per_page=per_page,
        retries=retries,
        retry_delay=retry_delay,
        refresh_interval=refresh_interval,
    )
    register_cell_path(
        _cell_name(row.cell),
        cell_path,
        description=row.description,
        refresh_interval=refresh_interval,
    )
    return {
        "index": row.index,
        "cell": row.cell,
        "url": row.url,
        "status": "ok",
        "receipt": receipt,
    }


def _error_row(row: BatchRow, exc: BaseException) -> dict[str, Any]:
    return {
        "index": row.index,
        "cell": row.cell,
        "url": row.url,
        "status": "error",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
    }


def run_batch(
    manifest_path: str | Path,
    *,
    concurrency: int = 2,
    per_page: int = 50,
    retries: int = 2,
    retry_delay: float = 0.25,
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    rows = validate_manifest_rows(_load_manifest(manifest_path))
    worker_count = min(concurrency, len(rows))

    def run(row: BatchRow) -> dict[str, Any]:
        try:
            return _build_one(
                row,
                per_page=per_page,
                retries=retries,
                retry_delay=retry_delay,
                refresh_interval=refresh_interval,
            )
        except (CaptureError, IntegrityError, OSError, ValueError, FileExistsError) as exc:
            return _error_row(row, exc)

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(run, rows))

    ok_count = sum(1 for row in results if row["status"] == "ok")
    return {
        "complete": ok_count == len(results),
        "requested_count": len(rows),
        "ok_count": ok_count,
        "error_count": len(results) - ok_count,
        "concurrency": worker_count,
        "rows": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="JSON array of {cell,url,description?} rows")
    parser.add_argument("--concurrency", type=int, default=2, help="maximum concurrent source builds")
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=0.25)
    parser.add_argument("--refresh-interval", type=int, default=DEFAULT_REFRESH_INTERVAL)
    args = parser.parse_args(argv)
    try:
        receipt = run_batch(
            args.manifest,
            concurrency=args.concurrency,
            per_page=args.per_page,
            retries=args.retries,
            retry_delay=args.retry_delay,
            refresh_interval=args.refresh_interval,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
