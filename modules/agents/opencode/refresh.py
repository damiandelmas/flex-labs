"""OpenCode cell refresh — signature scan over OpenCode SQLite DB."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from flex.modules.claude_code import run_enrichment
from flex.modules.claude_code.compile.worker import _batch_embed_chunks
from flex.modules.opencode.compile.worker import DEFAULT_OPENCODE_DB, compute_db_signature, transpile


_SIZE_KEY = "opencode_db_size"
_MTIME_KEY = "opencode_db_mtime_ns"
_SOURCE_KEY = "opencode_db_path"


def _source_from_meta(conn: sqlite3.Connection) -> Path:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (_SOURCE_KEY,)).fetchone()
    if row and row[0]:
        return Path(row[0])
    return DEFAULT_OPENCODE_DB


def _last_signature(conn: sqlite3.Connection) -> tuple[int, int]:
    def _get(key: str) -> int:
        row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
        if row and row[0]:
            try:
                return int(row[0])
            except (TypeError, ValueError):
                return 0
        return 0

    return _get(_SIZE_KEY), _get(_MTIME_KEY)


def _record_signature(conn: sqlite3.Connection, source: Path, db_size: int, db_mtime_ns: int) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (_SIZE_KEY, str(db_size)))
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (_MTIME_KEY, str(db_mtime_ns)))
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
            cur_size, cur_mtime_ns = compute_db_signature(source)
        except OSError:
            return {"chunks": 0, "sources": 0, "skipped": "stat failed"}

        changed = (cur_size, cur_mtime_ns) != _last_signature(conn)
        if dry_run:
            return {"dry_run": True, "needs_resync": changed}

        if not changed and not graph:
            return {"chunks": 0, "sources": 0, "skipped": "signature unchanged"}

        stats = transpile(source, conn)
        if stats.get("chunks", 0) > 0 or graph:
            try:
                _batch_embed_chunks(conn, quiet=True)
            except Exception as e:
                print(f"[opencode.refresh] embed failed: {e}", file=sys.stderr)
                conn.commit()
            try:
                run_enrichment(conn, cell_type="opencode")
            except Exception as e:
                print(f"[opencode.refresh] enrichment failed: {e}", file=sys.stderr)

        _record_signature(conn, source, cur_size, cur_mtime_ns)
        return {"sources": stats.get("sessions", 0), "chunks": stats.get("chunks", 0)}
    finally:
        conn.close()
