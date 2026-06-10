"""OpenCode SQLite transpiler.

Reads OpenCode's local SQLite store and emits Claude Code-canonical rows with
OpenCode-specific sidecars for native session/part metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from flex.modules.claude_code.compile.worker import (
    _ingest_file_body,
    _store_content_raw,
    ensure_source_exists,
    insert_chunk_atom,
    update_source_stats,
)

try:
    from flex.modules.soma.coding_agent import enrich_operation as soma_enrich_operation
except ImportError:  # pragma: no cover - older Flex core without shared bridge
    soma_enrich_operation = None


DEFAULT_OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


OPENCODE_OPTIONAL_TABLES_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS _types_opencode_session (
        source_id TEXT PRIMARY KEY,
        session_id TEXT,
        project_id TEXT,
        parent_id TEXT,
        slug TEXT,
        directory TEXT,
        title TEXT,
        version TEXT,
        summary_additions INTEGER,
        summary_deletions INTEGER,
        summary_files INTEGER,
        time_created INTEGER,
        time_updated INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_opencode_session_id ON _types_opencode_session(session_id)",
    """
    CREATE TABLE IF NOT EXISTS _types_opencode_part (
        chunk_id TEXT PRIMARY KEY,
        message_id TEXT,
        part_id TEXT,
        part_type TEXT,
        raw_tool TEXT,
        call_id TEXT,
        status TEXT,
        snapshot TEXT,
        reason TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_opencode_part_message ON _types_opencode_part(message_id)",
    "CREATE INDEX IF NOT EXISTS idx_opencode_part_type ON _types_opencode_part(part_type)",
    """
    CREATE TABLE IF NOT EXISTS _types_opencode_patch (
        chunk_id TEXT PRIMARY KEY,
        patch_hash TEXT,
        files_json TEXT,
        diff_json TEXT
    )
    """,
)


@dataclass(frozen=True)
class OpenCodeSession:
    id: str
    project_id: str | None
    parent_id: str | None
    slug: str | None
    directory: str | None
    title: str | None
    version: str | None
    summary_additions: int | None
    summary_deletions: int | None
    summary_files: int | None
    summary_diffs: str | None
    time_created: int | None
    time_updated: int | None


@dataclass(frozen=True)
class OpenCodeMessage:
    id: str
    session_id: str
    time_created: int | None
    time_updated: int | None
    data: dict[str, Any]


@dataclass(frozen=True)
class OpenCodePart:
    id: str
    message_id: str
    session_id: str
    time_created: int | None
    time_updated: int | None
    data: dict[str, Any]


def ensure_opencode_tables(conn: sqlite3.Connection) -> None:
    for ddl in OPENCODE_OPTIONAL_TABLES_DDL:
        conn.execute(ddl)
    conn.commit()


def _json_loads(value: str | bytes | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_id(session_id: str) -> str:
    return f"opencode:{session_id}"


def _chunk_dict(
    chunk_id: str,
    source_id: str,
    chunk_number: int,
    msg_type: str,
    role: str,
    content: str,
    timestamp: int,
    cwd: str | None,
    model: str | None = None,
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
        "model": model,
    }


def _as_epoch_millis(value: int | None) -> int:
    if not value:
        return int(time.time())
    # OpenCode stores ms timestamps in observed DB/export.
    return int(value / 1000) if value > 10_000_000_000 else int(value)


def _canonical_tool(raw_tool: str | None) -> str | None:
    if raw_tool == "bash":
        return "Bash"
    if raw_tool == "read":
        return "Read"
    if raw_tool == "apply_patch":
        return "Edit"
    return raw_tool


def _target_file(data: dict[str, Any]) -> str | None:
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    if inp.get("filePath"):
        return str(inp["filePath"])
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    files = metadata.get("files")
    if isinstance(files, list) and files:
        first = files[0]
        if isinstance(first, dict):
            return first.get("filePath") or first.get("relativePath")
    return None


def _tool_success(data: dict[str, Any]) -> bool | None:
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    status = state.get("status")
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    if "exit" in metadata:
        try:
            return int(metadata["exit"]) == 0
        except (TypeError, ValueError):
            return None
    if status == "completed":
        return True
    if status in {"error", "failed"}:
        return False
    return None


def _tool_content(data: dict[str, Any]) -> str:
    tool = data.get("tool") or "tool"
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    title = state.get("title")
    if tool == "bash":
        command = inp.get("command") or ""
        output = state.get("output") or metadata.get("output") or ""
        if output:
            return f"Bash: {command}\n{str(output).strip()}"
        return f"Bash: {command}".strip()
    if tool == "read":
        path = inp.get("filePath") or title or ""
        preview = metadata.get("preview") or state.get("output") or ""
        return f"Read: {path}\n{str(preview).strip()}".strip()
    if tool == "apply_patch":
        patch = inp.get("patchText") or metadata.get("diff") or state.get("output") or ""
        return f"Edit: {_target_file(data) or ''}\n{str(patch).strip()}".strip()
    raw = state.get("output") or title or json.dumps(data, ensure_ascii=False, sort_keys=True)
    return f"{tool}: {raw}".strip()


_CONTENT_RE = re.compile(r"<content>\n?(?P<body>.*?)\n?</content>", re.DOTALL)
_LINE_NO_RE = re.compile(r"^\s*\d+:\s?", re.MULTILINE)
_END_RE = re.compile(r"\n?\(End of file - total \d+ lines?\)\s*$")


def extract_read_file_body(output: str | None) -> str | None:
    """Extract clean file body text from OpenCode read tool output."""
    if not output:
        return None
    match = _CONTENT_RE.search(output)
    if not match:
        return None
    body = match.group("body")
    body = _END_RE.sub("", body)
    body = _LINE_NO_RE.sub("", body)
    return body if body.strip() else None


def _message_model(data: dict[str, Any]) -> str | None:
    provider = data.get("providerID")
    model = data.get("modelID")
    if model:
        return f"{provider}/{model}" if provider else str(model)
    model_obj = data.get("model")
    if isinstance(model_obj, dict):
        provider = model_obj.get("providerID")
        model = model_obj.get("modelID")
        if model:
            return f"{provider}/{model}" if provider else str(model)
    return None


def _message_cwd(data: dict[str, Any], session: OpenCodeSession) -> str | None:
    path = data.get("path")
    if isinstance(path, dict) and path.get("cwd"):
        return str(path["cwd"])
    return session.directory


def _part_timestamp(part: OpenCodePart, message: OpenCodeMessage, session: OpenCodeSession, offset: int) -> int:
    data = part.data
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    state_time = state.get("time") if isinstance(state.get("time"), dict) else {}
    for value in (state_time.get("start"), data.get("timestamp"), part.time_created, message.time_created, session.time_created):
        if isinstance(value, int):
            return _as_epoch_millis(value) + offset
    return int(time.time()) + offset


def _part_text(part: OpenCodePart) -> str | None:
    data = part.data
    ptype = data.get("type")
    if ptype in {"text", "reasoning"}:
        text = data.get("text")
        return str(text) if text else None
    if ptype == "tool":
        return _tool_content(data)
    if ptype == "patch":
        files = data.get("files")
        if isinstance(files, list):
            return "Patch: " + ", ".join(str(f) for f in files)
        return "Patch"
    return None


def _load_sessions(src: sqlite3.Connection, limit: int | None = None) -> list[OpenCodeSession]:
    sql = """
        SELECT id, project_id, parent_id, slug, directory, title, version,
               summary_additions, summary_deletions, summary_files,
               summary_diffs, time_created, time_updated
        FROM session
        ORDER BY time_created
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    out: list[OpenCodeSession] = []
    for row in src.execute(sql):
        out.append(OpenCodeSession(*row))
    return out


def _load_messages(src: sqlite3.Connection, session_id: str) -> list[OpenCodeMessage]:
    out: list[OpenCodeMessage] = []
    for row in src.execute(
        "SELECT id, session_id, time_created, time_updated, data FROM message WHERE session_id = ? ORDER BY time_created, id",
        (session_id,),
    ):
        out.append(OpenCodeMessage(row[0], row[1], row[2], row[3], _json_loads(row[4])))
    return out


def _load_parts(src: sqlite3.Connection, session_id: str) -> dict[str, list[OpenCodePart]]:
    out: dict[str, list[OpenCodePart]] = {}
    for row in src.execute(
        """
        SELECT id, message_id, session_id, time_created, time_updated, data
        FROM part
        WHERE session_id = ?
        ORDER BY time_created, id
        """,
        (session_id,),
    ):
        part = OpenCodePart(row[0], row[1], row[2], row[3], row[4], _json_loads(row[5]))
        out.setdefault(part.message_id, []).append(part)
    return out


def _record_session(conn: sqlite3.Connection, source_id: str, session: OpenCodeSession) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO _types_opencode_session
        (source_id, session_id, project_id, parent_id, slug, directory, title, version,
         summary_additions, summary_deletions, summary_files, time_created, time_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            session.id,
            session.project_id,
            session.parent_id,
            session.slug,
            session.directory,
            session.title,
            session.version,
            session.summary_additions,
            session.summary_deletions,
            session.summary_files,
            session.time_created,
            session.time_updated,
        ),
    )


def _record_part(conn: sqlite3.Connection, chunk_id: str, part: OpenCodePart) -> None:
    data = part.data
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    conn.execute(
        """
        INSERT OR REPLACE INTO _types_opencode_part
        (chunk_id, message_id, part_id, part_type, raw_tool, call_id, status, snapshot, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chunk_id,
            part.message_id,
            part.id,
            data.get("type"),
            data.get("tool"),
            data.get("callID"),
            state.get("status"),
            data.get("snapshot"),
            data.get("reason"),
        ),
    )


def _record_patch(conn: sqlite3.Connection, chunk_id: str, part: OpenCodePart) -> None:
    data = part.data
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    patch_text = data.get("patch") or inp.get("patchText")
    diff = metadata.get("diff") or patch_text or json.dumps(data, ensure_ascii=False, sort_keys=True)
    patch_hash = hashlib.sha256(str(diff).encode("utf-8", errors="replace")).hexdigest()[:16]
    files = data.get("files") or metadata.get("files")
    conn.execute(
        """
        INSERT OR REPLACE INTO _types_opencode_patch
        (chunk_id, patch_hash, files_json, diff_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            chunk_id,
            patch_hash,
            json.dumps(files, ensure_ascii=False, sort_keys=True) if files is not None else None,
            json.dumps({"diff": diff}, ensure_ascii=False, sort_keys=True),
        ),
    )


def _sync_session(
    src: sqlite3.Connection,
    conn: sqlite3.Connection,
    session: OpenCodeSession,
) -> int:
    ensure_opencode_tables(conn)
    source_id = _source_id(session.id)
    ensure_source_exists(conn, source_id, cwd=session.directory, title=session.title)
    conn.execute(
        """
        UPDATE _raw_sources
        SET source = ?,
            primary_cwd = COALESCE(primary_cwd, ?),
            file_date = COALESCE(file_date, ?),
            model = COALESCE(model, ?)
        WHERE source_id = ?
        """,
        (f"opencode:{session.id}", session.directory, str(session.time_created or ""), None, source_id),
    )
    _record_session(conn, source_id, session)

    last_num = conn.execute(
        """
        SELECT COALESCE(MAX(tm.chunk_number), 0)
        FROM _types_message tm
        JOIN _edges_source es ON tm.chunk_id = es.chunk_id
        WHERE es.source_id = ?
        """,
        (source_id,),
    ).fetchone()[0]

    messages = _load_messages(src, session.id)
    parts_by_message = _load_parts(src, session.id)
    inserted = 0
    chunk_number = 0
    for message in messages:
        role = message.data.get("role") or "assistant"
        model = _message_model(message.data)
        cwd = _message_cwd(message.data, session)
        if model:
            conn.execute("UPDATE _raw_sources SET model = COALESCE(model, ?) WHERE source_id = ?", (model, source_id))
        for part in parts_by_message.get(message.id, []):
            content = _part_text(part)
            if not content or not content.strip():
                continue
            chunk_number += 1
            if chunk_number <= last_num:
                continue

            ptype = part.data.get("type")
            msg_type = "tool_call" if ptype in {"tool", "patch"} else ("user_prompt" if role == "user" else "assistant")
            out_role = "user" if role == "user" else "assistant"
            chunk_id = f"{source_id}_{chunk_number}"
            timestamp = _part_timestamp(part, message, session, chunk_number)
            chunk = _chunk_dict(chunk_id, source_id, chunk_number, msg_type, out_role, content.strip(), timestamp, cwd, model=model)

            if ptype == "tool":
                raw_tool = part.data.get("tool")
                chunk["tool_name"] = _canonical_tool(raw_tool)
                chunk["target_file"] = _target_file(part.data)
                chunk["success"] = _tool_success(part.data)

            insert_chunk_atom(conn, chunk)
            if ptype == "tool" and soma_enrich_operation:
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
            conn.execute("UPDATE _edges_source SET source_type = 'opencode' WHERE chunk_id = ?", (chunk_id,))
            inserted += 1
            _record_part(conn, chunk_id, part)

            if ptype == "tool":
                state = part.data.get("state") if isinstance(part.data.get("state"), dict) else {}
                raw_tool = str(part.data.get("tool") or "opencode_tool")
                raw = state.get("output") or json.dumps(part.data, ensure_ascii=False, sort_keys=True)
                _store_content_raw(conn, chunk_id, str(raw), raw_tool, timestamp)
                if raw_tool == "read":
                    body = extract_read_file_body(state.get("output"))
                    target = _target_file(part.data)
                    if body and target:
                        _ingest_file_body(conn, chunk_id, target, body, source_id, timestamp)
                if raw_tool == "apply_patch":
                    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
                    if inp.get("patchText"):
                        _store_content_raw(conn, chunk_id, str(inp["patchText"]), "apply_patch_input", timestamp)
                    _record_patch(conn, chunk_id, part)
            elif ptype == "patch":
                _record_patch(conn, chunk_id, part)

    return inserted


def transpile(
    source_path: Path,
    conn: sqlite3.Connection,
    progress_cb=None,
    limit: int | None = None,
    commit_every: int = 50,
) -> dict[str, float | int]:
    """Read OpenCode SQLite DB and write CC-canonical rows."""
    if not source_path.exists():
        raise FileNotFoundError(f"opencode db not found: {source_path}")

    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30.0)
    src.row_factory = sqlite3.Row
    src.execute("PRAGMA busy_timeout=30000")
    ensure_opencode_tables(conn)
    t0 = time.time()
    n_sessions = 0
    n_chunks = 0
    try:
        sessions = _load_sessions(src, limit=limit)
        total = len(sessions)
        for i, session in enumerate(sessions, start=1):
            before = conn.execute("SELECT COUNT(*) FROM _types_opencode_session").fetchone()[0]
            added = _sync_session(src, conn, session)
            after = conn.execute("SELECT COUNT(*) FROM _types_opencode_session").fetchone()[0]
            n_chunks += added
            n_sessions += max(0, after - before)
            if i % commit_every == 0 or i == total:
                conn.commit()
            if progress_cb:
                progress_cb(i, total, n_sessions, n_chunks, time.time() - t0)
    finally:
        src.close()
    return {"sessions": n_sessions, "chunks": n_chunks, "elapsed": time.time() - t0}


def compute_db_signature(source_path: Path) -> tuple[int, int]:
    """Return (db_size_bytes, db_mtime_ns) for refresh drift detection."""
    st = source_path.stat()
    return int(st.st_size), int(st.st_mtime_ns)
