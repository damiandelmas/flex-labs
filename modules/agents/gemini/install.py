"""gemini install hook — transpiler spec + Claude Code substrate."""

from __future__ import annotations

from flex.modules.claude_code.coding_agent_install import (
    register_common_args,
    run_from_spec,
)
from flex.modules.gemini.compile.worker import DEFAULT_GEMINI_DIR


MODULE_SUMMARY = "index Gemini CLI chats — programmable memory for Gemini CLI sessions"

MODULE = {
    "cell_type": "gemini",
    "maturity": "preview",
    "license_intent": "MIT-compatible Labs module",
    "release_posture": "private Labs, public later",
    "description": "Gemini CLI session provenance. Each doc is a session, each chunk is a user prompt, model turn, thought, or tool call. Native records preserved losslessly.",
    "default_cell_name": "gemini",
    "source_arg": "--gemini-dir",
    "source_label": "gemini chats dir",
    "source_help": "Path to Gemini CLI tmp dir (default: ~/.gemini/tmp)",
    "default_source": DEFAULT_GEMINI_DIR,
    "missing_hint": "install the Gemini CLI and run at least one session.",
    "transpile": "flex.modules.gemini.compile.worker:transpile",
    "signature": "flex.modules.gemini.compile.worker:dir_signature",
    "signature_meta_keys": ("gemini_dir_signature",),
    "source_meta_key": "gemini_dir_path",
    "refresh_module": "flex.modules.gemini.refresh",
    "watch_pattern": "*/chats/session-*.json",
    "substrate": "claude_code",
    "soma_level": "L3",
    "views_from": ("claude_code",),
    "presets_from": ("claude_code", "soma"),
    "instructions_from": ("gemini", "claude_code"),
    "enrichment_stubs_from": "claude_code",
    "skill": "flex:sessions:gemini",
    "query_examples": ("@orient", "@digest", "@file path='src/foo.py'"),
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
