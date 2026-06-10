"""Aider install hook — transpiler spec + Claude Code substrate."""

from __future__ import annotations

from pathlib import Path

from flex.modules.claude_code.coding_agent_install import (
    register_common_args,
    run_from_spec,
)


MODULE_SUMMARY = "index Aider chat histories — programmable memory for Aider"

DEFAULT_AIDER_SOURCE = Path.home()

MODULE = {
    "cell_type": "aider",
    "maturity": "preview",
    "license_intent": "MIT-compatible Labs module",
    "release_posture": "private Labs, public later",
    "description": "Aider chat history provenance. Each doc is an Aider session, each chunk is a prompt, assistant turn, or status/tool event.",
    "default_cell_name": "aider",
    "source_arg": "--aider-dir",
    "source_label": "aider source",
    "source_help": "Path to Aider chat file or directory (default: ~)",
    "default_source": DEFAULT_AIDER_SOURCE,
    "missing_hint": "run Aider with chat history enabled at least once.",
    "transpile": "flex.modules.aider.compile.worker:transpile",
    "signature": "flex.modules.aider.compile.worker:compute_dir_signature",
    "signature_meta_keys": ("aider_total_size", "aider_file_count"),
    "source_meta_key": "aider_source_path",
    "refresh_module": "flex.modules.aider.refresh",
    "watch_pattern": "**/.aider*.chat*.md",
    "substrate": "claude_code",
    "soma_level": "L2",
    "views_from": ("claude_code",),
    "presets_from": ("claude_code", "soma"),
    "enrichment_stubs_from": "claude_code",
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
