"""Gemini CLI transpiler — the only gemini-specific code in the module.

Reads ~/.gemini/tmp/<projectHash>/chats/session-*.json (read-only) and emits
claude_code-canonical rows PLUS a lossless `_native_records` layer.

Raw-first architecture (2026-06-17): every native record is preserved verbatim
in `_native_records`; the CC substrate projection (chunks / edges / types) is
DERIVED from it, for embedding + graph. ACP views are generated downstream by
the substrate's `regenerate_views()` (run inside `run_enrichment`). The native
layer is the storage truth; the projection is re-derivable.

Native format (Gemini CLI 0.29.x, verified against live sessions):
  file:  <tmp>/<projectHash>/chats/session-*.json
  top:   sessionId, projectHash, startTime, lastUpdated, summary, messages[]
  msg:   {id, type, content, timestamp, model?, tokens?, thoughts?, toolCalls?}
         type: 'user' | 'gemini' (assistant turn) | 'info' (CLI notice)
  tool:  {id, name, args, result, status, timestamp, resultDisplay,
          displayName, description, renderOutputAsMarkdown}
  tokens:{input, output, cached, thoughts, tool, total}

Wire-format notes:
1. A `gemini` message bundles MANY toolCalls in one record. Each toolCall is
   projected as its OWN tool_call chunk (id `{sid}_{n}_t{k}`) so it gets a
   distinct row in `_edges_tool_ops` (whose chunk_id is PRIMARY KEY).
2. read_file returns the body in `toolCall.result`; write_file/replace carry
   the body in `toolCall.args`. Both feed `_ingest_file_body`.
3. `info` messages (CLI update notices) are kept losslessly in `_native_records`
   but NOT projected — projection is opinionated, the native layer is complete.
"""

from __future__ import annotations

import ast
import glob
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Vendored CC helpers — used as a dependency, not modified.
from flex.modules.claude_code.compile.worker import (
    _ingest_file_body,
    _store_content_raw,
    ensure_source_exists,
    insert_chunk_atom,
    update_source_stats,
)

try:
    from flex.modules.soma.coding_agent import enrich_operation as soma_enrich_operation
except ImportError:  # pragma: no cover - older Flex installs without shared bridge
    soma_enrich_operation = None


DEFAULT_GEMINI_DIR = Path.home() / ".gemini" / "tmp"
_SESSION_GLOB = "*/chats/session-*.json"


# ── Tool-name mapping (gemini bare name → canonical CC) ──────────────────────

_TOOL_NAME_MAP: dict[str, str] = {
    "read_file":           "Read",
    "read_many_files":     "Read",
    "write_file":          "Write",
    "replace":             "Edit",
    "run_shell_command":   "Bash",
    "list_directory":      "LS",
    "glob":                "Glob",
    "grep_search":         "Grep",
    "search_file_content": "Grep",
    "google_web_search":   "WebSearch",
    "web_fetch":           "WebFetch",
    "write_todos":         "TodoWrite",
}

_PATH_KEYS = ("file_path", "path", "absolute_path", "filename")
_BODY_KEYS = ("content", "file_text", "new_string", "new_str", "text")


# ── Native lossless layer ────────────────────────────────────────────────────

_NATIVE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS _native_records (
        provider TEXT NOT NULL,
        native_id TEXT NOT NULL,
        source_id TEXT,
        record_type TEXT,          -- 'session' | 'message'
        payload_json TEXT NOT NULL,
        payload_hash TEXT,
        position INTEGER,
        PRIMARY KEY (provider, native_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_native_source ON _native_records(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_native_type ON _native_records(record_type)",
)

_GEMINI_SESSION_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS _types_gemini_session (
        source_id TEXT PRIMARY KEY,
        session_id TEXT,
        project_hash TEXT,
        start_time TEXT,
        last_updated TEXT,
        summary TEXT,
        message_count INTEGER,
        tool_call_count INTEGER,
        models_json TEXT,
        total_input_tokens INTEGER,
        total_output_tokens INTEGER,
        total_cached_tokens INTEGER,
        total_thought_tokens INTEGER,
        total_tokens INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gemini_session_hash ON _types_gemini_session(project_hash)",
)


def ensure_gemini_tables(conn: sqlite3.Connection) -> None:
    """Create native + gemini-specific tables. Idempotent."""
    for ddl in _NATIVE_DDL + _GEMINI_SESSION_DDL:
        conn.execute(ddl)


def _store_native(
    conn: sqlite3.Connection,
    native_id: str,
    source_id: Optional[str],
    record_type: str,
    payload: object,
    position: Optional[int],
) -> None:
    """Preserve a native record verbatim. The lossless storage truth."""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT OR REPLACE INTO _native_records "
        "(provider, native_id, source_id, record_type, payload_json, payload_hash, position) "
        "VALUES ('gemini', ?, ?, ?, ?, ?, ?)",
        (native_id, source_id, record_type, raw, h, position),
    )


# ── Coercion / extraction helpers ────────────────────────────────────────────

def _coerce_dict(val) -> dict:
    """Gemini `args`/`result` may be a dict or a stringified dict. Normalize."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return {}
        try:
            out = json.loads(s)
            return out if isinstance(out, dict) else {"_value": out}
        except (json.JSONDecodeError, ValueError):
            try:
                out = ast.literal_eval(s)
                return out if isinstance(out, dict) else {"_value": out}
            except (ValueError, SyntaxError):
                return {"_value": s}
    return {}


def _map_tool_name(raw_name: str) -> str:
    if not raw_name:
        return "unknown"
    return _TOOL_NAME_MAP.get(raw_name, raw_name)


def _target_file(args: dict) -> Optional[str]:
    for k in _PATH_KEYS:
        v = args.get(k)
        if v:
            return str(v)
    return None


def _body_from_args(canonical: str, args: dict) -> Optional[str]:
    if canonical not in ("Write", "Edit"):
        return None
    for k in _BODY_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _flatten_result(val) -> str:
    """Flatten a toolCall.result (str / list / dict) into searchable text."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        bits: list[str] = []
        for item in val:
            if isinstance(item, dict):
                fr = item.get("functionResponse") or {}
                resp = fr.get("response") if isinstance(fr, dict) else None
                if isinstance(resp, dict) and resp.get("output") is not None:
                    bits.append(str(resp["output"]))
                    continue
                txt = item.get("text") or item.get("output")
                bits.append(str(txt) if txt is not None
                            else json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            else:
                bits.append(str(item))
        return "\n".join(bits)
    if isinstance(val, dict):
        if val.get("output") is not None:
            return str(val["output"])
        if val.get("text") is not None:
            return str(val["text"])
        return json.dumps(val, ensure_ascii=False, separators=(",", ":"))
    return str(val)


def _iso_to_epoch(value) -> Optional[int]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        s = str(value).replace("Z", "+00:00")
        if "T" not in s and "+" not in s:
            s = s.replace(" ", "T") + "+00:00"
        return int(datetime.fromisoformat(s).timestamp())
    except (ValueError, TypeError):
        return None


def _insert_chunk(conn: sqlite3.Connection, chunk: dict, session_id: str) -> bool:
    """Insert a chunk; update source stats only when actually inserted."""
    chunk["embedding"] = None
    before = conn.total_changes
    try:
        insert_chunk_atom(conn, chunk)
    except Exception as e:
        print(f"[gemini] chunk insert error: {e}", file=sys.stderr)
        return False
    if conn.total_changes > before:
        try:
            update_source_stats(conn, session_id, chunk)
        except Exception as e:
            print(f"[gemini] source stats error: {e}", file=sys.stderr)
        return True
    return False


# ── Per-session transpile ─────────────────────────────────────────────────────

def _sync_session(session: dict, conn: sqlite3.Connection, source_id: str) -> int:
    """Translate one Gemini session-file JSON into native records + CC chunks.

    `source_id` is the per-FILE identity (the filename stem). Gemini stores each
    project under both a friendly-name and a hash dir, and splits a logical
    session into timestamped segment files that share a `sessionId`. Keying
    storage by the filename stem dedups the path-copies (same stem) and keeps
    distinct segments distinct; the logical `sessionId` is kept as metadata.
    """
    ensure_gemini_tables(conn)

    sess_uid = session.get("sessionId") or session.get("id")
    if not source_id:
        return 0
    project_hash = session.get("projectHash")
    summary = session.get("summary")
    cwd = session.get("_workspace")  # set by caller if discoverable
    messages = session.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    ensure_source_exists(conn, source_id, cwd=cwd, title=summary)

    # ① Lossless: session metadata (without the messages array) preserved verbatim.
    session_meta = {k: v for k, v in session.items() if k != "messages"}
    _store_native(conn, f"{source_id}:session", source_id, "session", session_meta, None)

    models: set[str] = set()
    tok = {"input": 0, "output": 0, "cached": 0, "thoughts": 0, "total": 0}
    tool_call_count = 0
    inserted = 0
    pos = 0

    for mi, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        mtype = (msg.get("type") or "").lower()
        msg_id = f"{source_id}:m{mi}"
        ts_int = _iso_to_epoch(msg.get("timestamp")) or int(time.time())

        # ① Lossless: every message preserved verbatim, including 'info'.
        _store_native(conn, msg_id, source_id, "message", msg, mi)

        model = msg.get("model")
        if model:
            models.add(str(model))
        mtok = msg.get("tokens") or {}
        if isinstance(mtok, dict):
            for k in ("input", "output", "cached", "thoughts", "total"):
                v = mtok.get(k)
                if isinstance(v, (int, float)):
                    tok[k] += int(v)

        content = msg.get("content") or ""
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)
        content = str(content).strip()
        thoughts = msg.get("thoughts")

        # ── Projection (②): map types → CC chunks. 'info' is not projected. ──
        if mtype == "user":
            if content:
                if _insert_chunk(conn, {
                    "id": f"{source_id}_{pos}", "doc_id": source_id, "chunk_number": pos,
                    "type": "user_prompt", "content": content, "tool_name": None,
                    "target_file": None, "success": None, "timestamp": ts_int, "role": "user",
                    "cwd": cwd, "git_branch": None, "parent_uuid": None, "is_sidechain": 0,
                    "entry_uuid": msg_id, "branch_id": 0,
                }, source_id):
                    inserted += 1
                pos += 1

        elif mtype in ("gemini", "model", "assistant"):
            # Assistant turn text (+ thoughts captured to _raw_content for search).
            body = content
            if thoughts and not body:
                body = str(thoughts) if not isinstance(thoughts, (dict, list)) \
                    else json.dumps(thoughts, ensure_ascii=False)
            if body:
                chunk_id = f"{source_id}_{pos}"
                if _insert_chunk(conn, {
                    "id": chunk_id, "doc_id": source_id, "chunk_number": pos,
                    "type": "assistant", "content": body, "tool_name": None,
                    "target_file": None, "success": None, "timestamp": ts_int, "role": "assistant",
                    "cwd": cwd, "git_branch": None, "parent_uuid": None, "is_sidechain": 0,
                    "entry_uuid": msg_id, "branch_id": 0,
                }, source_id):
                    inserted += 1
                if thoughts:
                    tblock = thoughts if isinstance(thoughts, str) \
                        else json.dumps(thoughts, ensure_ascii=False)
                    if tblock and len(tblock) > 10:
                        _store_content_raw(conn, chunk_id, tblock, "_thinking", ts_int)
                pos += 1

            # Each toolCall → its own tool_call chunk + edge.
            for tk, tc in enumerate(msg.get("toolCalls") or []):
                if not isinstance(tc, dict):
                    continue
                tool_call_count += 1
                raw_name = tc.get("name") or "unknown"
                canonical = _map_tool_name(raw_name)
                args = _coerce_dict(tc.get("args"))
                tfile = _target_file(args)
                status = (tc.get("status") or "").lower()
                ok = 1 if status in ("success", "ok", "completed", "") else 0
                tc_ts = _iso_to_epoch(tc.get("timestamp")) or ts_int

                # Tool chunk content: name + file/command + result display.
                pieces = [canonical]
                if tfile:
                    pieces.append(tfile)
                if canonical == "Bash":
                    cmd = args.get("command")
                    if cmd:
                        pieces.append(str(cmd))
                disp = tc.get("resultDisplay")
                if isinstance(disp, str) and disp:
                    pieces.append(disp)
                tool_text = " ".join(p for p in pieces if p).strip() or canonical

                tchunk_id = f"{source_id}_{pos}_t{tk}"
                if _insert_chunk(conn, {
                    "id": tchunk_id, "doc_id": source_id, "chunk_number": pos,
                    "type": "tool_call", "content": tool_text, "tool_name": canonical,
                    "target_file": tfile, "success": bool(ok), "timestamp": tc_ts,
                    "role": "assistant", "cwd": cwd, "git_branch": None, "parent_uuid": None,
                    "is_sidechain": 0, "entry_uuid": tc.get("id") or None, "branch_id": 0,
                }, source_id):
                    inserted += 1

                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO _edges_tool_ops "
                        "(chunk_id, tool_name, target_file, success, cwd, git_branch) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (tchunk_id, canonical, tfile, ok, cwd, None),
                    )
                    if soma_enrich_operation:
                        soma_enrich_operation(conn, {
                            "chunk_id": tchunk_id, "tool_name": canonical,
                            "target_file": tfile, "cwd": cwd, "source_id": source_id,
                        })
                except Exception as e:
                    print(f"[gemini] tool_ops insert error: {e}", file=sys.stderr)

                # Full args + result → _raw_content (searchable provenance).
                raw_args = json.dumps(args, ensure_ascii=False)
                if len(raw_args) > 10:
                    _store_content_raw(conn, tchunk_id, raw_args, canonical, tc_ts)
                result_text = _flatten_result(tc.get("result"))
                if result_text and len(result_text) > 10:
                    _store_content_raw(conn, tchunk_id, result_text, canonical, tc_ts)

                # File bodies: Write/Edit from args; Read from result.
                body = _body_from_args(canonical, args)
                if body and tfile:
                    try:
                        _ingest_file_body(conn, tchunk_id, tfile, body, source_id, tc_ts)
                    except Exception as e:
                        print(f"[gemini] file body (args) error: {e}", file=sys.stderr)
                elif canonical == "Read" and tfile and result_text and len(result_text) > 50:
                    try:
                        _ingest_file_body(conn, tchunk_id, tfile, result_text, source_id, tc_ts)
                    except Exception as e:
                        print(f"[gemini] file body (read) error: {e}", file=sys.stderr)

                pos += 1
        # 'info' and unknown types: lossless only, no projection.

    conn.execute(
        "UPDATE _edges_source SET source_type = 'gemini' WHERE source_id = ?",
        (source_id,),
    )

    # Gemini session sidecar (high-value queryable fields).
    conn.execute(
        """
        INSERT OR REPLACE INTO _types_gemini_session (
            source_id, session_id, project_hash, start_time, last_updated, summary,
            message_count, tool_call_count, models_json,
            total_input_tokens, total_output_tokens, total_cached_tokens,
            total_thought_tokens, total_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id, sess_uid, project_hash,
            session.get("startTime"), session.get("lastUpdated"), summary,
            len(messages), tool_call_count, json.dumps(sorted(models)),
            tok["input"], tok["output"], tok["cached"], tok["thoughts"], tok["total"],
        ),
    )

    if inserted == 0:
        conn.execute(
            "DELETE FROM _raw_sources WHERE source_id = ? AND message_count = 0",
            (source_id,),
        )
    return inserted


# ── Workspace discovery (best-effort, from message content) ──────────────────

def _discover_workspace(session: dict) -> Optional[str]:
    import os
    import re
    messages = session.get("messages") or []
    for msg in messages[:6]:
        if not isinstance(msg, dict):
            continue
        c = msg.get("content")
        if not isinstance(c, str):
            continue
        m = re.search(r"AGENTS\.md instructions for ([^\n]+)", c)
        if m:
            return m.group(1).strip()
        m = re.search(r"Working directory:?\s*([^\n]+)", c)
        if m:
            return m.group(1).strip()

    # Fallback: infer the workspace from absolute file paths touched by toolCalls.
    # `project` derives from cwd in ensure_source_exists, so a good cwd populates it.
    paths: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("toolCalls") or []:
            if not isinstance(tc, dict):
                continue
            fp = _target_file(_coerce_dict(tc.get("args")))
            if fp and fp.startswith("/"):
                paths.append(os.path.dirname(fp))
    if paths:
        try:
            common = os.path.commonpath(paths)
        except ValueError:
            common = None
        # Reject too-shallow ancestors (e.g. "/", "/home", "/home/user").
        if common and common.count("/") >= 3:
            return common
    return None


# ── Public entry points ────────────────────────────────────────────────────

def dir_signature(source: Path) -> int:
    """Total size of all session files — the dir's source signature."""
    source = Path(source).expanduser()
    total = 0
    for f in glob.glob(str(source / _SESSION_GLOB)):
        try:
            total += Path(f).stat().st_size
        except OSError:
            continue
    return total


def transpile(
    source_path: Path,
    conn: sqlite3.Connection,
    progress_cb=None,
    limit: Optional[int] = None,
    commit_every: int = 25,
) -> dict:
    """Read Gemini CLI chats dir and write native records + CC chunks. Idempotent.

    Signature matches install/refresh call sites: (source_path, conn, progress_cb).
    """
    source_path = Path(source_path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(
            f"gemini chats dir not found at {source_path}. "
            "Install the Gemini CLI and run at least one session first."
        )

    files = sorted(glob.glob(str(source_path / _SESSION_GLOB)))
    if limit:
        files = files[: int(limit)]
    total = len(files)

    ensure_gemini_tables(conn)
    t0 = time.time()
    n_sessions = 0
    n_chunks = 0
    seen_stems: set[str] = set()

    for i, fpath in enumerate(files, 1):
        # Gemini stores each project under both a friendly-name and a hash dir,
        # so the same session file appears at multiple paths. The filename stem
        # uniquely identifies a session segment — dedup path-copies by it.
        source_id = Path(fpath).stem
        if source_id in seen_stems:
            if progress_cb:
                progress_cb(i, total, n_sessions, n_chunks, time.time() - t0)
            continue
        seen_stems.add(source_id)

        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                session = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[gemini] skip {fpath}: {e}", file=sys.stderr)
            continue
        if not isinstance(session, dict) or not session.get("messages"):
            continue

        session["_workspace"] = _discover_workspace(session)
        try:
            added = _sync_session(session, conn, source_id)
            n_chunks += added
            if added > 0:
                n_sessions += 1
        except Exception as e:
            print(f"[gemini] session {fpath} failed: {e}", file=sys.stderr)

        if i % commit_every == 0 or i == total:
            conn.commit()
        if progress_cb:
            progress_cb(i, total, n_sessions, n_chunks, time.time() - t0)

    conn.commit()
    return {"sessions": n_sessions, "chunks": n_chunks, "elapsed": time.time() - t0}
