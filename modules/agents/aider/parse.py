"""Aider transcript parser.

Parses Aider's Markdown chat history format into recoverable blocks and
classifies high-value tool/status events. This module deliberately avoids
importing Aider: the installed Aider parser is a useful oracle for tests, but
Flex ingestion must not depend on a user's Aider package version.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal


BlockRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class AiderBlock:
    role: BlockRole
    content: str
    start_line: int
    end_line: int
    raw: str
    event_kind: str | None = None
    event: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AiderSession:
    source_path: str
    session_index: int
    start_line: int
    end_line: int
    started_at: str | None
    command: str | None
    version: str | None
    model: str | None
    git_repo: str | None
    repo_map: str | None
    blocks: list[AiderBlock]


SESSION_RE = re.compile(r"^# aider chat started at (?P<started_at>.+?)\s*$")
TOKEN_RE = re.compile(
    r"Tokens:\s+(?P<sent>[0-9.]+[kM]?)\s+sent,\s+"
    r"(?P<received>[0-9.]+[kM]?)\s+received\."
)
COMMIT_RE = re.compile(r"^(?:Commit|> Commit)\s+(?P<hash>[0-9a-f]+)\s+(?P<message>.+?)\s*$")
UNDO_RE = re.compile(r"^(?:Removed:|> Removed:)\s+(?P<hash>[0-9a-f]+)\s+(?P<message>.+?)\s*$")
EDIT_RE = re.compile(r"Applied edit to (?P<path>.+?)\s*$")
NO_EDIT_RE = re.compile(r"Did not apply edit to (?P<path>.+?)\s+\(--dry-run\)")
ADDED_CHAT_RE = re.compile(r"Added (?P<path>.+?) to the chat")
READ_ONLY_RE = re.compile(r"Added (?P<path>.+?) to read-only files\.|(?P<rel>.+?) \(read only\)")
DROPPED_RE = re.compile(r"(?:Dropped|Removed) (?P<path>.+?) from the chat")
SAVE_RE = re.compile(r"Saved commands to (?P<path>.+?)\s*$")
EXEC_RE = re.compile(r"Executing: (?P<command>/.+?)\s*$")
RUN_ADDED_RE = re.compile(r"Added (?P<lines>\d+) lines? of output to the chat\.")
FILE_PROMPT_RE = re.compile(r"(Create new file|Add file to the chat)\? .*?: (?P<answer>[a-z])\s*$")
MODE_RE = re.compile(r"Model:\s+(?P<model>.+?)\s+with\s+(?P<edit_format>.+?)\s+edit format")


COMMAND_KINDS = {
    "/add": "context_file",
    "/read-only": "context_file",
    "/drop": "context_file",
    "/run": "shell",
    "/test": "test",
    "/lint": "lint",
    "/git": "git",
    "/map": "repo_map",
    "/tokens": "usage",
    "/clear": "history",
    "/reset": "history",
    "/save": "history",
    "/load": "history",
    "/ask": "mode",
    "/code": "mode",
    "/architect": "mode",
    "/chat-mode": "mode",
    "/copy-context": "context_dump",
    "/web": "web",
    "/commit": "git",
    "/undo": "git",
    "/diff": "git",
    "/settings": "settings",
}


def parse_chat_file(path: str | Path) -> list[AiderSession]:
    source = Path(path)
    return parse_chat_markdown(source.read_text(encoding="utf-8", errors="replace"), source_path=str(source))


def parse_chat_markdown(text: str, source_path: str = "<memory>") -> list[AiderSession]:
    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, str | None]] = []
    for idx, line in enumerate(lines, start=1):
        match = SESSION_RE.match(line.rstrip("\n"))
        if match:
            starts.append((idx, match.group("started_at")))

    if not starts:
        return []

    sessions: list[AiderSession] = []
    for session_index, (start_line, started_at) in enumerate(starts):
        end_line = (starts[session_index + 1][0] - 1) if session_index + 1 < len(starts) else len(lines)
        chunk = lines[start_line - 1:end_line]
        blocks = _parse_blocks(chunk, base_line=start_line)
        meta = _session_metadata(blocks)
        sessions.append(
            AiderSession(
                source_path=source_path,
                session_index=session_index,
                start_line=start_line,
                end_line=end_line,
                started_at=started_at,
                command=meta.get("command"),
                version=meta.get("version"),
                model=meta.get("model"),
                git_repo=meta.get("git_repo"),
                repo_map=meta.get("repo_map"),
                blocks=blocks,
            )
        )
    return sessions


def parse_input_history(path: str | Path) -> list[dict[str, str | int]]:
    source = Path(path)
    if not source.exists():
        return []
    entries: list[dict[str, str | int]] = []
    current_ts: str | None = None
    current: list[str] = []
    start_line = 0
    for idx, line in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if line.startswith("# "):
            if current:
                entries.append(
                    {
                        "timestamp": current_ts or "",
                        "content": "\n".join(current),
                        "start_line": start_line,
                        "end_line": idx - 1,
                    }
                )
                current = []
            current_ts = line[2:].strip()
            start_line = idx
        elif line.startswith("+"):
            current.append(line[1:])
    if current:
        entries.append(
            {
                "timestamp": current_ts or "",
                "content": "\n".join(current),
                "start_line": start_line,
                "end_line": len(source.read_text(encoding="utf-8", errors="replace").splitlines()),
            }
        )
    return entries


def parse_llm_history(path: str | Path) -> list[dict[str, str | int]]:
    source = Path(path)
    if not source.exists():
        return []
    records: list[dict[str, str | int]] = []
    current_role: str | None = None
    current_ts: str | None = None
    current: list[str] = []
    start_line = 0
    marker = re.compile(r"^(TO LLM|LLM RESPONSE)\s+(.+?)\s*$")
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    for idx, line in enumerate(lines, start=1):
        match = marker.match(line)
        if match:
            if current_role is not None:
                records.append(
                    {
                        "role": current_role,
                        "timestamp": current_ts or "",
                        "content": "\n".join(current).rstrip(),
                        "start_line": start_line,
                        "end_line": idx - 1,
                    }
                )
            current_role = "to_llm" if match.group(1) == "TO LLM" else "llm_response"
            current_ts = match.group(2)
            current = []
            start_line = idx
        else:
            current.append(line)
    if current_role is not None:
        records.append(
            {
                "role": current_role,
                "timestamp": current_ts or "",
                "content": "\n".join(current).rstrip(),
                "start_line": start_line,
                "end_line": len(lines),
            }
        )
    return records


def read_analytics_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    out: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {"event": "_invalid_json", "raw": line}
        event["_line"] = line_number
        out.append(event)
    return out


def iter_event_blocks(sessions: Iterable[AiderSession]) -> Iterable[AiderBlock]:
    for session in sessions:
        for block in session.blocks:
            if block.event_kind:
                yield block


def coverage(sessions: Iterable[AiderSession]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in iter_event_blocks(sessions):
        counts[block.event_kind or "unknown"] = counts.get(block.event_kind or "unknown", 0) + 1
    return counts


def unknown_tool_lines(sessions: Iterable[AiderSession]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in sessions:
        for block in session.blocks:
            if block.role == "tool" and block.event_kind == "raw_status":
                rows.append(
                    {
                        "source_path": session.source_path,
                        "session_index": session.session_index,
                        "start_line": block.start_line,
                        "content": block.content,
                    }
                )
    return rows


def _parse_blocks(lines: list[str], base_line: int) -> list[AiderBlock]:
    blocks: list[AiderBlock] = []
    current_role: BlockRole | None = None
    current: list[str] = []
    current_start = base_line

    def flush(end_line: int) -> None:
        nonlocal current_role, current, current_start
        if current_role is None or not current:
            current_role = None
            current = []
            return
        raw = "".join(current)
        content = _content_for_role(current_role, raw)
        if content.strip() or current_role == "system":
            event_kind, event = classify_block(current_role, content)
            blocks.append(
                AiderBlock(
                    role=current_role,
                    content=content,
                    start_line=current_start,
                    end_line=end_line,
                    raw=raw,
                    event_kind=event_kind,
                    event=event,
                )
            )
        current_role = None
        current = []

    for offset, line in enumerate(lines):
        line_no = base_line + offset
        role = _line_role(line)
        if role == "tool":
            flush(line_no - 1)
            content = _content_for_role("tool", line)
            event_kind, event = classify_block("tool", content)
            blocks.append(
                AiderBlock(
                    role="tool",
                    content=content,
                    start_line=line_no,
                    end_line=line_no,
                    raw=line,
                    event_kind=event_kind,
                    event=event,
                )
            )
            current_role = None
            current = []
            continue
        if role != current_role:
            flush(line_no - 1)
            current_role = role
            current_start = line_no
        current.append(line)
    flush(base_line + len(lines) - 1)
    return blocks


def _line_role(line: str) -> BlockRole:
    stripped = line.rstrip("\n")
    if SESSION_RE.match(stripped):
        return "system"
    if stripped.startswith("#### "):
        return "user"
    if stripped.startswith("> "):
        return "tool"
    return "assistant"


def _content_for_role(role: BlockRole, raw: str) -> str:
    lines = raw.splitlines()
    if role == "tool":
        return "\n".join(line[2:] if line.startswith("> ") else line for line in lines).rstrip()
    if role == "user":
        out: list[str] = []
        for line in lines:
            out.append(line[5:] if line.startswith("#### ") else line)
        return "\n".join(out).rstrip()
    if role == "system":
        match = SESSION_RE.match(lines[0] if lines else "")
        return match.group("started_at") if match else raw.strip()
    return raw.rstrip()


def classify_block(role: BlockRole, content: str) -> tuple[str | None, dict[str, Any]]:
    if role == "system":
        return "session_start", {}
    if role == "user":
        command = _leading_command(content)
        event: dict[str, Any] = {"command": command}
        if command:
            name = command.split()[0]
            event["command_name"] = name
            event["command_kind"] = COMMAND_KINDS.get(name, "command")
        return "user_prompt", event
    if role != "tool":
        return (None, {})

    lines = [line.rstrip() for line in content.splitlines()]
    first = next((line for line in lines if line.strip()), "")
    event: dict[str, Any] = {"raw": content}

    if _looks_like_aider_launch(first):
        event.update(_startup_command_event(first))
        return "startup", event
    if first.startswith("Aider v"):
        event["version"] = first.removeprefix("Aider v").strip()
        return "startup", event
    if first.startswith("Model:") or first.startswith("Main model:"):
        event["model"] = first.split(":", 1)[1].strip()
        match = MODE_RE.search(first)
        if match:
            event["model_name"] = match.group("model")
            event["edit_format"] = match.group("edit_format")
        return "startup", event
    if first.startswith("Git repo:"):
        event["git_repo"] = first.split(":", 1)[1].strip()
        return "startup", event
    if first.startswith("Repo-map:"):
        event["repo_map"] = first.split(":", 1)[1].strip()
        return "repo_map", event
    if first.startswith("/"):
        command = first
        name = command.split()[0]
        event["command"] = command
        event["command_name"] = name
        return COMMAND_KINDS.get(name, "command"), event

    token = TOKEN_RE.search(content)
    if token:
        event.update(token.groupdict())
        return "usage", event
    commit = COMMIT_RE.search(content)
    if commit:
        event.update(commit.groupdict())
        return "git_commit", event
    undo = UNDO_RE.search(content)
    if undo:
        event.update(undo.groupdict())
        return "git_undo", event
    no_edit = NO_EDIT_RE.search(content)
    if no_edit:
        event.update(no_edit.groupdict())
        return "dry_run", event
    edit = EDIT_RE.search(content)
    if edit:
        event.update(edit.groupdict())
        return "edit_applied", event
    save = SAVE_RE.search(content)
    if save:
        event.update(save.groupdict())
        return "history_save", event
    exec_match = EXEC_RE.search(content)
    if exec_match:
        event.update(exec_match.groupdict())
        return "history_load_exec", event
    if "All chat history cleared" in content or "All files dropped and chat history cleared" in content:
        return "history_clear", event
    if "Restored previous conversation history" in content:
        return "history_restore", event
    if "Copied code context to clipboard" in content:
        return "context_dump", event
    if "No dirty files to lint" in content or "flake8" in content or "lint" in content.lower():
        return "lint", event
    if "FAILED " in content or "FAILURES" in content or "AssertionError" in content:
        return "test_failure", event
    if run_added := RUN_ADDED_RE.search(content):
        event.update(run_added.groupdict())
        return "shell_output_added", event
    if "Here are summaries of some files present in my git repository" in content:
        return "repo_map", event
    if "Scraping http" in content or "Install playwright" in content or "Playwright" in content:
        return "web", event
    if file_prompt := FILE_PROMPT_RE.search(content):
        event.update(file_prompt.groupdict())
        return "confirmation", event
    if added := ADDED_CHAT_RE.search(content):
        event.update(added.groupdict())
        return "context_file", event
    if read_only := READ_ONLY_RE.search(content):
        event["path"] = (read_only.group("path") or read_only.group("rel") or "").strip()
        return "context_file", event
    if dropped := DROPPED_RE.search(content):
        event.update(dropped.groupdict())
        return "context_file", event
    if first.startswith("$ ") or "tokens remaining" in content or "tokens total" in content:
        return "usage", event
    if re.match(r"^[?MADRCU!]{1,2}\s+|^[0-9a-f]{7,}\s+", first):
        return "git", event
    return "raw_status", event


def _looks_like_aider_launch(line: str) -> bool:
    first = line.split(maxsplit=1)[0] if line else ""
    return (
        (first.startswith("/") and first.endswith("/aider"))
        or first == "aider"
        or ("aider " in line and "--" in line)
    )


def _leading_command(content: str) -> str | None:
    stripped = content.strip()
    if stripped.startswith("/"):
        return stripped.splitlines()[0]
    return None


def _startup_command_event(line: str) -> dict[str, Any]:
    return {
        "command": line,
        "message_mode": "--message " in line or "--message=" in line,
        "message_file": "--message-file " in line or "--message-file=" in line,
        "dry_run": "--dry-run" in line,
        "restore_history": "--restore-chat-history" in line,
        "no_git": "--no-git" in line,
        "show_repo_map": "--show-repo-map" in line,
    }


def _session_metadata(blocks: list[AiderBlock]) -> dict[str, str]:
    meta: dict[str, str] = {}
    for block in blocks:
        if block.role != "tool":
            continue
        if block.event_kind == "startup":
            command = block.event.get("command")
            version = block.event.get("version")
            model = block.event.get("model")
            git_repo = block.event.get("git_repo")
            if command and "command" not in meta:
                meta["command"] = str(command)
            if version and "version" not in meta:
                meta["version"] = str(version)
            if model:
                meta["model"] = str(model)
            if git_repo and "git_repo" not in meta:
                meta["git_repo"] = str(git_repo)
        elif block.event_kind == "repo_map":
            repo_map = block.event.get("repo_map")
            if repo_map and "repo_map" not in meta:
                meta["repo_map"] = str(repo_map)
    return meta
