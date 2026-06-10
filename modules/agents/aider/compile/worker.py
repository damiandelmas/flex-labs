"""Aider transcript transpiler.

Reads Aider Markdown history files and emits Claude Code-canonical rows:
`_raw_chunks`, `_raw_sources`, `_edges_source`, `_edges_tool_ops`,
`_types_message`, `_raw_content`, and `_edges_raw_content`.

The goal is the same substrate as Claude Code/Codex, with Aider-specific
sidecars for fields that do not fit the common contract.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from flex.modules.aider.parse import AiderBlock, AiderSession, parse_chat_file
from flex.modules.claude_code.compile.worker import (
    _store_content_raw,
    ensure_source_exists,
    insert_chunk_atom,
    update_source_stats,
)

try:
    from flex.modules.soma.coding_agent import enrich_operation as soma_enrich_operation
except ImportError:  # pragma: no cover - older Flex core without shared bridge
    soma_enrich_operation = None


AIDER_OPTIONAL_TABLES_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS _types_aider_session (
        source_id TEXT PRIMARY KEY,
        source_path TEXT,
        session_index INTEGER,
        start_line INTEGER,
        end_line INTEGER,
        started_at TEXT,
        command TEXT,
        version TEXT,
        model TEXT,
        git_repo TEXT,
        repo_map TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aider_session_path ON _types_aider_session(source_path)",
    """
    CREATE TABLE IF NOT EXISTS _types_aider_turn (
        chunk_id TEXT PRIMARY KEY,
        source_id TEXT,
        role TEXT,
        event_kind TEXT,
        start_line INTEGER,
        end_line INTEGER,
        command_name TEXT,
        command_kind TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aider_turn_source ON _types_aider_turn(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_aider_turn_event ON _types_aider_turn(event_kind)",
    """
    CREATE TABLE IF NOT EXISTS _types_aider_tool_event (
        chunk_id TEXT PRIMARY KEY,
        event_kind TEXT,
        event_json TEXT,
        raw_status INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS _types_aider_usage (
        chunk_id TEXT PRIMARY KEY,
        sent TEXT,
        received TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS _types_aider_git_event (
        chunk_id TEXT PRIMARY KEY,
        event_kind TEXT,
        commit_hash TEXT,
        message TEXT
    )
    """,
)


def ensure_aider_tables(conn: sqlite3.Connection) -> None:
    """Create Aider-specific optional tables. Idempotent."""
    for ddl in AIDER_OPTIONAL_TABLES_DDL:
        conn.execute(ddl)
    conn.commit()


def _source_id(session: AiderSession) -> str:
    stable = f"{session.source_path}\0{session.session_index}\0{session.started_at or ''}"
    return "aider:" + hashlib.sha1(stable.encode("utf-8", errors="replace")).hexdigest()[:24]


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        if "T" not in text and "+" not in text:
            text = text.replace(" ", "T")
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def _session_title(session: AiderSession) -> str | None:
    for block in session.blocks:
        if block.role == "user" and block.content.strip():
            return block.content.strip()[:250]
    return None


def _source_cwd(session: AiderSession) -> str | None:
    try:
        return str(Path(session.source_path).resolve().parent)
    except OSError:
        return str(Path(session.source_path).parent)


def _chunk_dict(
    chunk_id: str,
    source_id: str,
    chunk_number: int,
    msg_type: str,
    role: str,
    content: str,
    timestamp: int,
    cwd: str | None,
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "doc_id": source_id,
        "chunk_number": chunk_number,
        "type": msg_type,
        "content": content,
        "tool_name": None,
        "target_file": None,
        "success": None,
        "timestamp": timestamp,
        "role": role,
        "cwd": cwd,
        "git_branch": None,
        "parent_uuid": None,
        "is_sidechain": 0,
        "entry_uuid": None,
        "branch_id": 0,
    }


def _tool_op(block: AiderBlock) -> tuple[str, str | None, bool | None] | None:
    kind = block.event_kind
    event = block.event or {}
    if not kind:
        return None
    if kind == "edit_applied":
        return ("Edit", event.get("path"), True)
    if kind == "dry_run":
        return ("Edit", event.get("path"), False)
    if kind in {"shell", "test", "lint"}:
        return ("Bash", None, True)
    if kind in {"git", "git_commit", "git_undo"}:
        return ("Bash", None, True)
    if kind == "web":
        return ("WebFetch", None, True)
    if kind == "repo_map":
        return ("Read", None, True)
    if kind == "context_file":
        return ("Read", event.get("path"), True)
    if kind == "context_dump":
        return ("Read", None, True)
    if kind in {"history_save", "history_load_exec", "history_clear", "history_restore"}:
        return ("NotebookEdit", event.get("path"), True)
    if kind == "usage":
        return ("TokenUsage", None, True)
    if kind in {"startup", "confirmation", "raw_status"}:
        return ("AiderStatus", None, None)
    return ("AiderStatus", None, None)


def _content_for_chunk(block: AiderBlock) -> str:
    if block.role != "tool":
        return block.content
    if block.event_kind and block.event_kind != "raw_status":
        return f"{block.event_kind}: {block.content}"
    return block.content


def _message_type(block: AiderBlock) -> tuple[str, str]:
    if block.role == "user":
        return ("user_prompt", "user")
    if block.role == "assistant":
        return ("assistant", "assistant")
    if block.role == "system":
        return ("system", "system")
    return ("tool_call", "assistant")


def _compact_event(block: AiderBlock) -> str:
    event = dict(block.event or {})
    event["role"] = block.role
    event["start_line"] = block.start_line
    event["end_line"] = block.end_line
    event["event_kind"] = block.event_kind
    return json.dumps(event, ensure_ascii=False, sort_keys=True)


def _sync_aider_session(session: AiderSession, conn: sqlite3.Connection) -> int:
    ensure_aider_tables(conn)
    source_id = _source_id(session)
    cwd = _source_cwd(session)
    title = _session_title(session)
    started_ts = _iso_to_epoch(session.started_at) or int(time.time())

    ensure_source_exists(conn, source_id, cwd=cwd, title=title)
    conn.execute(
        """
        UPDATE _raw_sources
        SET source = ?,
            model = COALESCE(?, model),
            primary_cwd = COALESCE(primary_cwd, ?),
            file_date = COALESCE(file_date, ?)
        WHERE source_id = ?
        """,
        (f"aider:{session.source_path}", session.model, cwd, session.started_at, source_id),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO _types_aider_session
        (source_id, source_path, session_index, start_line, end_line, started_at,
         command, version, model, git_repo, repo_map)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            session.source_path,
            session.session_index,
            session.start_line,
            session.end_line,
            session.started_at,
            session.command,
            session.version,
            session.model,
            session.git_repo,
            session.repo_map,
        ),
    )

    last_num = conn.execute(
        """
        SELECT COALESCE(MAX(tm.chunk_number), 0)
        FROM _types_message tm
        JOIN _edges_source es ON tm.chunk_id = es.chunk_id
        WHERE es.source_id = ?
        """,
        (source_id,),
    ).fetchone()[0]

    inserted = 0
    for chunk_number, block in enumerate(session.blocks, start=1):
        if chunk_number <= last_num:
            continue
        content = _content_for_chunk(block).strip()
        if not content:
            continue
        msg_type, role = _message_type(block)
        chunk_id = f"{source_id}_{chunk_number}"
        timestamp = started_ts + max(0, chunk_number - 1)
        chunk = _chunk_dict(chunk_id, source_id, chunk_number, msg_type, role, content, timestamp, cwd)

        tool_op = _tool_op(block)
        if tool_op:
            chunk["tool_name"], chunk["target_file"], chunk["success"] = tool_op

        insert_chunk_atom(conn, chunk)
        if tool_op and soma_enrich_operation:
            soma_enrich_operation(
                conn,
                {
                    "chunk_id": chunk_id,
                    "tool_name": chunk["tool_name"],
                    "target_file": chunk["target_file"],
                    "cwd": cwd,
                    "source_id": source_id,
                    "position": chunk_number,
                },
            )
        update_source_stats(conn, source_id, chunk)
        inserted += 1

        conn.execute(
            """
            INSERT OR REPLACE INTO _types_aider_turn
            (chunk_id, source_id, role, event_kind, start_line, end_line,
             command_name, command_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                source_id,
                block.role,
                block.event_kind,
                block.start_line,
                block.end_line,
                block.event.get("command_name"),
                block.event.get("command_kind"),
            ),
        )
        if block.event_kind:
            conn.execute(
                """
                INSERT OR REPLACE INTO _types_aider_tool_event
                (chunk_id, event_kind, event_json, raw_status)
                VALUES (?, ?, ?, ?)
                """,
                (chunk_id, block.event_kind, _compact_event(block), 1 if block.event_kind == "raw_status" else 0),
            )
        if block.event_kind == "usage":
            conn.execute(
                "INSERT OR REPLACE INTO _types_aider_usage (chunk_id, sent, received) VALUES (?, ?, ?)",
                (chunk_id, block.event.get("sent"), block.event.get("received")),
            )
        if block.event_kind in {"git_commit", "git_undo"}:
            conn.execute(
                """
                INSERT OR REPLACE INTO _types_aider_git_event
                (chunk_id, event_kind, commit_hash, message)
                VALUES (?, ?, ?, ?)
                """,
                (chunk_id, block.event_kind, block.event.get("hash"), block.event.get("message")),
            )
        if block.role == "tool":
            _store_content_raw(conn, chunk_id, block.raw, block.event_kind or "aider_tool", timestamp)

    if inserted:
        conn.execute("UPDATE _edges_source SET source_type = 'aider' WHERE source_id = ?", (source_id,))
    return inserted


def _sync_aider_chat_file(path: Path, conn: sqlite3.Connection) -> int:
    """Sync all appended sessions in one Aider chat Markdown file."""
    inserted = 0
    for session in parse_chat_file(path):
        inserted += _sync_aider_session(session, conn)
    return inserted


def transpile(
    source_path: Path,
    conn: sqlite3.Connection,
    progress_cb=None,
    limit: int | None = None,
    commit_every: int = 50,
) -> dict[str, float | int]:
    """Read an Aider history file or directory and write CC-canonical rows."""
    if not source_path.exists():
        raise FileNotFoundError(f"aider source not found: {source_path}")

    if source_path.is_file():
        files = [source_path]
    else:
        files = sorted(source_path.rglob(".aider*.chat*.md"))
    if limit:
        files = files[: int(limit)]

    ensure_aider_tables(conn)
    total = len(files)
    t0 = time.time()
    n_sessions = 0
    n_chunks = 0

    for i, path in enumerate(files, start=1):
        before_sessions = conn.execute("SELECT COUNT(*) FROM _types_aider_session").fetchone()[0]
        added = _sync_aider_chat_file(path, conn)
        after_sessions = conn.execute("SELECT COUNT(*) FROM _types_aider_session").fetchone()[0]
        n_chunks += added
        n_sessions += max(0, after_sessions - before_sessions)
        if i % commit_every == 0 or i == total:
            conn.commit()
        if progress_cb:
            progress_cb(i, total, n_sessions, n_chunks, time.time() - t0)

    return {"sessions": n_sessions, "chunks": n_chunks, "elapsed": time.time() - t0}


def compute_dir_signature(source_path: Path) -> tuple[int, int]:
    """Return (total_size_bytes, file_count) for cheap drift detection."""
    files = [source_path] if source_path.is_file() else source_path.rglob(".aider*.chat*.md")
    total = 0
    count = 0
    for path in files:
        try:
            total += path.stat().st_size
            count += 1
        except OSError:
            continue
    return total, count
