"""Atomic full refresh of a registered XOLogic capture-backed cell."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .worker import build_cell


def _metadata(path: Path) -> dict[str, str]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
        return dict(db.execute("SELECT key,value FROM _metadata"))


def refresh(cell_path: str, *, capture_path: str | None = None,
            graph: bool = False, dry_run: bool = False,
            since_days: int | None = None, **_kwargs) -> dict:
    """Recompile the complete configured capture; graph/since are accepted no-ops."""
    target = Path(cell_path)
    meta = _metadata(target) if target.exists() else {}
    source_value = capture_path or meta.get("capture_path")
    if not source_value:
        raise ValueError("capture_path is required for first refresh")
    source = Path(source_value)
    if dry_run:
        from .worker import inspect_capture
        return {"dry_run": True, **inspect_capture(source)}
    return build_cell(source, target, store_name=meta.get("store_name"))


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--capture-path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(refresh(args.cell, capture_path=args.capture_path, dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
