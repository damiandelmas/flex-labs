"""Instant install hook — v4: one cell, the filesystem HEAD projection.

Compiles a UNION of folder selections into a single no-embed projection cell
(default name: fs). Each --path adds a selection to the cell's recipe; every
run wipes and recompiles the union. FTS5 + structural SQL, unlisted,
regenerable from its own stored recipe. SOMA file UUIDs stamped per source.

No embeddings, no graph, ever: run = compile stays free because nothing
durable is paid into the cell. Moves and renames are handled by recompiling —
the walk finds files wherever they now live. Regenerate, never heal.
"""

from __future__ import annotations

import json
import re


CLI_NAME = "instant"
MODULE_SUMMARY = "compile folder selections into one no-embed projection cell — FTS5 + SQL, unlisted, regenerable"

MODULE = {
    "cell_type": "instant",
    "maturity": "labs",
    "auth": "none",
    "selection": "set of folder paths (--path / --remove)",
    "resolver": "walk_and_chunk@v1",
    "description": "Instant no-embed projection of selected folders. keyword()/SQL only. Unlisted. Regenerate, never heal.",
    "query_examples": ("@orient", "SELECT snippet FROM keyword('term') LIMIT 5"),
}

_FS_IDENTITY_DDL = """
CREATE TABLE IF NOT EXISTS _edges_fs_identity (
    source_id TEXT PRIMARY KEY,
    file_uuid TEXT
);
"""


def _add_arg(parser, *flags, **kwargs) -> None:
    existing = {opt for action in parser._actions for opt in action.option_strings}
    if not any(flag in existing for flag in flags):
        parser.add_argument(*flags, **kwargs)


def register_args(parser) -> None:
    _add_arg(parser, "--path", default=None,
             help="Add a folder to the cell's selections and recompile")
    _add_arg(parser, "--regen", nargs="?", const="fs", default=None, metavar="NAME",
             help="Recompile a cell from its stored selections (default cell: fs)")
    _add_arg(parser, "--remove", default=None, metavar="PATH",
             help="Remove a selection and recompile")
    _add_arg(parser, "--name", default=None,
             help="Target cell (default: fs)")
    _add_arg(parser, "--description", default=None,
             help="Cell description")
    _add_arg(parser, "--no-soma", action="store_true",
             help="Skip SOMA file UUID stamping")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()
    return slug or "fs"


def _error(console, message: str) -> None:
    console.print(f"[red]instant:[/red] {message}")


def _load_selections(name: str) -> list[str]:
    """Read the stored recipe. Legacy v3 cells migrate via their source_path key."""
    from flex.core import get_meta, open_cell_readonly
    from flex.registry import resolve_cell

    cell_path = resolve_cell(name)
    if cell_path is None:
        return []
    ro = open_cell_readonly(str(cell_path))
    try:
        raw = get_meta(ro, "selections")
        if raw:
            return json.loads(raw)
        legacy = get_meta(ro, "source_path")
        return [legacy] if legacy else []
    finally:
        ro.close()


def run(args, console) -> None:
    """Compile the union of selections into one unlisted, no-embed cell."""
    from datetime import datetime, timezone
    from pathlib import Path

    from rich.panel import Panel
    from rich.text import Text

    from flex.core import set_meta
    from flex.registry import FLEX_HOME, register_cell
    from flex.sdk import _walk_and_chunk, create, ingest, register, source

    regen = getattr(args, "regen", None)
    name = (getattr(args, "name", None)
            or (regen if isinstance(regen, str) else None)
            or "fs")

    # ── resolve selections (recipe ± edits) ──
    selections = _load_selections(name)
    if regen is not None and not selections and not getattr(args, "path", None):
        _error(console, f"cell '{name}' has no stored selections — add one with --path")
        return

    add_path = getattr(args, "path", None)
    if add_path:
        selections = sorted(set(selections) | {str(Path(add_path).expanduser().resolve())})
    remove_path = getattr(args, "remove", None)
    if remove_path:
        target = str(Path(remove_path).expanduser().resolve())
        if target not in selections:
            _error(console, f"'{target}' is not a selection of cell '{name}'")
            return
        selections = [s for s in selections if s != target]

    if not selections:
        _error(console, "no selections — use --path FOLDER (or --regen NAME)")
        return
    missing = [s for s in selections if not Path(s).is_dir()]
    if missing:
        _error(console, "selection folder(s) missing — recipe NOT modified, cell untouched:")
        for s in missing:
            console.print(f"    {s}")
        return

    desc = (getattr(args, "description", None)
            or f"{name} — instant projection of {len(selections)} selection(s)")

    # ── union walk, dedup by absolute path (first selection to claim a file wins) ──
    by_file: dict[str, list] = {}
    skipped_dupes = 0
    for root_str in selections:
        root = Path(root_str)
        claimed_before = set(by_file)          # files owned by earlier selections
        for chunk in _walk_and_chunk(root):
            rel = chunk.pop("_source", "default")
            abs_path = str((root / rel).resolve()) if rel != "default" else root_str
            if abs_path in claimed_before:
                skipped_dupes += 1
                continue
            by_file.setdefault(abs_path, []).append(chunk)

    if not by_file:
        _error(console, "no indexable content in any selection — cell untouched")
        return
    total_chunks = sum(len(v) for v in by_file.values())

    # ── compile (cell is a cache; selections + folders are the truth) ──
    cells_dir = FLEX_HOME / "cells" / "labs"
    cells_dir.mkdir(parents=True, exist_ok=True)
    db_path = cells_dir / f"{name}.db"
    for stale in (db_path, db_path.with_suffix(".db-wal"), db_path.with_suffix(".db-shm")):
        if stale.exists():
            stale.unlink()

    db = create(name, desc, cell_type="instant", db_path=db_path, schema=_FS_IDENTITY_DDL)

    for abs_path, file_chunks in by_file.items():
        source(db, abs_path, Path(abs_path).name)
        ingest(db, abs_path, file_chunks)

    # ── SOMA file identity (machine-global spine; must never fail a compile) ──
    soma_status = "skipped (--no-soma)"
    if not getattr(args, "no_soma", False):
        try:
            from flex.modules.soma.lib.identity.file_identity import get_instance
            uuids = get_instance().assign_batch(list(by_file))
            db.executemany(
                "INSERT OR REPLACE INTO _edges_fs_identity (source_id, file_uuid) VALUES (?, ?)",
                [(p, u) for p, u in uuids.items() if u],
            )
            soma_status = f"{len(uuids)} file UUIDs stamped"
        except ImportError:
            soma_status = "absent (no edges)"
        except Exception as e:  # noqa: BLE001 — identity must never fail a compile
            soma_status = f"failed, skipped ({type(e).__name__})"

    # ── recipe stamp: the cell carries its own derivation ──
    set_meta(db, "selections", json.dumps(selections))
    set_meta(db, "resolver", "walk_and_chunk@v1")
    set_meta(db, "profile", "instant")
    set_meta(db, "compiled_at", datetime.now(timezone.utc).isoformat())

    # embed() and graph() are deliberately absent, permanently.
    register(db, name, desc, cell_type="instant")
    register_cell(name, str(db_path), unlisted=True)
    db.commit()
    db.close()

    panel = Text()
    panel.append("Instant cell compiled.\n\n", style="cyan")
    panel.append("Cell        ", style="")
    panel.append(f"{name}\n", style="green")
    panel.append("Selections  ", style="")
    shown = selections[:5]
    panel.append(f"{shown[0]}\n", style="green")
    for s in shown[1:]:
        panel.append(f"            {s}\n", style="green")
    if len(selections) > 5:
        panel.append(f"            (+{len(selections) - 5} more)\n", style="green")
    panel.append("Compiled    ", style="")
    dupe_note = f" ({skipped_dupes} overlap dupes skipped)" if skipped_dupes else ""
    panel.append(f"{total_chunks} chunks from {len(by_file)} files{dupe_note}\n", style="green")
    panel.append("Identity    ", style="")
    panel.append(f"{soma_status}\n", style="green")
    panel.append("Mode        ", style="")
    panel.append("no embeddings · no graph · unlisted (query by exact name)\n\n", style="yellow")
    panel.append("  flex core search --cell ", style="bold")
    panel.append(f"{name} ", style="bold green")
    panel.append('"@orient"\n', style="bold")
    panel.append("  flex core search --cell ", style="bold")
    panel.append(f"{name} ", style="bold green")
    panel.append("\"SELECT snippet FROM keyword('term') LIMIT 5\"\n\n", style="bold")
    panel.append("  grow:    flex init --module instant --path FOLDER\n", style="dim")
    panel.append("  regen:   flex init --module instant --regen", style="dim")
    panel.append(f"{'' if name == 'fs' else ' ' + name}\n", style="dim green")
    panel.append("  shrink:  flex init --module instant --remove FOLDER\n", style="dim")
    console.print(Panel(panel, padding=(1, 2), highlight=False))
