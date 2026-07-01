"""Gemini CLI cell refresh — signature-poll against the chats dir.

Registry hook: lifecycle='watch' + refresh_module='flex.modules.gemini.refresh'.
The flex daemon dry-runs this on the local watch cadence and calls
`refresh(cell_path, ...)` when the source signature (total session-file bytes)
changes. All heavy lifting (embed, enrichment, view regen) is reused from
claude_code.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from flex.modules.claude_code import run_enrichment
from flex.modules.claude_code.compile.worker import _batch_embed_chunks
from flex.modules.gemini.compile.worker import (
    DEFAULT_GEMINI_DIR,
    dir_signature,
    transpile,
)


_SIG_KEY = "gemini_dir_signature"
_SOURCE_KEY = "gemini_dir_path"


def _source_from_meta(conn: sqlite3.Connection) -> Path:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (_SOURCE_KEY,)).fetchone()
    if row and row[0]:
        return Path(row[0])
    return DEFAULT_GEMINI_DIR


def _last_sig(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (_SIG_KEY,)).fetchone()
    if row and row[0]:
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return 0
    return 0


def _record_sig(conn: sqlite3.Connection, source: Path, sig: int) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (_SIG_KEY, str(sig)))
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

        current_sig = dir_signature(source)

        if dry_run:
            return {"dry_run": True, "needs_resync": current_sig > _last_sig(conn)}

        if current_sig <= _last_sig(conn) and not graph:
            return {"chunks": 0, "sources": 0, "skipped": "signature unchanged"}

        stats = transpile(source, conn)

        if stats.get("chunks", 0) > 0 or graph:
            try:
                _batch_embed_chunks(conn, quiet=True)
            except Exception as e:
                print(f"[gemini.refresh] embed failed: {e}", file=sys.stderr)
                conn.commit()
            try:
                run_enrichment(conn, cell_type="gemini")
            except Exception as e:
                print(f"[gemini.refresh] enrichment failed: {e}", file=sys.stderr)

        _record_sig(conn, source, current_sig)
        return {"sources": stats.get("sessions", 0), "chunks": stats.get("chunks", 0)}
    finally:
        conn.close()
