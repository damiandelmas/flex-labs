"""OpenCode install hook — transpiler spec + Claude Code substrate."""

from __future__ import annotations

from flex.modules.claude_code.coding_agent_install import (
    register_common_args,
    run_from_spec,
)
from flex.modules.opencode.compile.worker import DEFAULT_OPENCODE_DB


MODULE_SUMMARY = "index OpenCode SQLite sessions — programmable memory for OpenCode"

MODULE = {
    "cell_type": "opencode",
    "maturity": "preview",
    "license_intent": "MIT-compatible Labs module",
    "release_posture": "private Labs, public later",
    "description": "OpenCode session provenance from the local SQLite store. Each doc is an OpenCode session, with messages, parts, tool calls, patches, and read-derived file bodies.",
    "default_cell_name": "opencode",
    "source_arg": "--opencode-db",
    "source_label": "OpenCode DB",
    "source_help": "Path to OpenCode SQLite DB (default: ~/.local/share/opencode/opencode.db)",
    "default_source": DEFAULT_OPENCODE_DB,
    "missing_hint": "run OpenCode at least once, or pass --opencode-db.",
    "transpile": "flex.modules.opencode.compile.worker:transpile",
    "signature": "flex.modules.opencode.compile.worker:compute_db_signature",
    "signature_meta_keys": ("opencode_db_size", "opencode_db_mtime_ns"),
    "source_meta_key": "opencode_db_path",
    "refresh_module": "flex.modules.opencode.refresh",
    "substrate": "claude_code",
    "soma_level": "L3",
    "views_from": ("claude_code",),
    "presets_from": ("claude_code", "soma"),
    "enrichment_stubs_from": "claude_code",
    "query_examples": ("@orient", "@digest", "SELECT tool_name, success, COUNT(*) FROM messages WHERE tool_name IS NOT NULL GROUP BY tool_name, success"),
}


def register_args(parser) -> None:
    register_common_args(
        parser,
        source_flag=MODULE["source_arg"],
        source_help=MODULE["source_help"],
        default_name=MODULE["default_cell_name"],
    )


def run(args, console) -> None:
    run_from_spec(args, console, MODULE)
