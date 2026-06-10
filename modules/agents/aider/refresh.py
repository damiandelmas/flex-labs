"""Aider cell refresh — signature scan over Aider chat Markdown files."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from flex.modules.aider.compile.worker import compute_dir_signature, transpile
from flex.modules.claude_code import run_enrichment
from flex.modules.claude_code.compile.worker import _batch_embed_chunks


DEFAULT_AIDER_SOURCE = Path.home()
_SIZE_KEY = "aider_total_size"
_COUNT_KEY = "aider_file_count"
_SOURCE_KEY = "aider_source_path"


def _source_from_meta(conn: sqlite3.Connection) -> Path:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (_SOURCE_KEY,)).fetchone()
    if row and row[0]:
        return Path(row[0])
    return DEFAULT_AIDER_SOURCE


def _last_signature(conn: sqlite3.Connection) -> tuple[int, int]:
    def _get(key: str) -> int:
        row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
        if row and row[0]:
            try:
                return int(row[0])
            except (TypeError, ValueError):
                return 0
        return 0

    return _get(_SIZE_KEY), _get(_COUNT_KEY)


def _record_signature(conn: sqlite3.Connection, source: Path, total_size: int, file_count: int) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (_SIZE_KEY, str(total_size)))
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (_COUNT_KEY, str(file_count)))
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (_SOURCE_KEY, str(source)))
    conn.commit()


def refresh(cell_path: str, graph: bool = False, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(str(cell_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        source = _source_from_meta(conn)
        if not source.exists():
            return {"chunks": 0, "sources": 0, "skipped": "source missing"}

        try:
            cur_size, cur_count = compute_dir_signature(source)
        except OSError:
            return {"chunks": 0, "sources": 0, "skipped": "stat failed"}

        last_size, last_count = _last_signature(conn)
        changed = (cur_size != last_size) or (cur_count != last_count)

        if dry_run:
            return {"dry_run": True, "needs_resync": changed}

        if not changed and not graph:
            return {"chunks": 0, "sources": 0, "skipped": "signature unchanged"}

        stats = transpile(source, conn)

        if stats.get("chunks", 0) > 0 or graph:
            try:
                _batch_embed_chunks(conn, quiet=True)
            except Exception as e:
                print(f"[aider.refresh] embed failed: {e}", file=sys.stderr)
                conn.commit()
            try:
                run_enrichment(conn, cell_type="aider")
            except Exception as e:
                print(f"[aider.refresh] enrichment failed: {e}", file=sys.stderr)

        _record_signature(conn, source, cur_size, cur_count)
        return {"sources": stats.get("sessions", 0), "chunks": stats.get("chunks", 0)}
    finally:
        conn.close()
