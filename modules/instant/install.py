"""Instant install hook — v5: recursive-node projection (tree metadata + content-hash).

Compiles a UNION of folder selections into a single no-embed projection cell
(default name: fs). Each --path adds a selection to the cell's recipe; every run
wipes and recompiles the union. FTS5 + structural SQL, unlisted, regenerable from
its own stored recipe. SOMA file UUIDs stamped per source.

No embeddings, no graph, ever: run = compile stays free because nothing durable is
paid into the cell. Moves and renames are handled by recompiling. Regenerate, never
heal.

v5 adds, purely additively (parity with v4 holds — leaves are byte-identical):
  - `_types_instant` (chunk_id PK → auto-joins into the `chunks` view): persists the
    section_title / position / depth / container_id that `chunk_file_body` already
    computes and the SDK ingest otherwise drops, plus a per-leaf content_hash (the
    future embed key; mirrors claude_code's _edges_content_identity).
  - a stock `subtree(:root)` recursive-CTE preset over `_edges_tree` — the query
    artifact for container subtrees (dormant at depth-1; the seat for multi-level).
"""

from __future__ import annotations

import hashlib
import json
import re


CLI_NAME = "instant"
MODULE_SUMMARY = "compile folder selections into one no-embed projection cell — FTS5 + SQL, tree metadata, unlisted, regenerable"

MODULE = {
    "cell_type": "instant",
    "maturity": "labs",
    "auth": "none",
    "selection": "set of folder paths (--path / --remove)",
    "resolver": "node_tree@v1",
    "description": "Instant no-embed projection of selected folders. keyword()/SQL only, with section_title/position/depth/container_id/content_hash. Unlisted. Regenerate, never heal.",
    "query_examples": ("@orient", "SELECT section_title, position FROM chunks LIMIT 5"),
}

_FS_IDENTITY_DDL = """
CREATE TABLE IF NOT EXISTS _edges_fs_identity (
    source_id TEXT PRIMARY KEY,
    file_uuid TEXT
);
"""

# v5: leaf metadata table — the dominant flex convention (_types_*). chunk_id PK
# means the view generator auto-joins these columns into the `chunks` view.
_TYPES_DDL = """
CREATE TABLE IF NOT EXISTS _types_instant (
    chunk_id      TEXT PRIMARY KEY,
    section_title TEXT,
    position      INTEGER,
    depth         INTEGER,
    container_id  TEXT,
    content_hash  TEXT
);
"""

# Call graph (Python, nest/config): caller def -> callee def (callee_id NULL = external/unresolved).
_CALL_DDL = """
CREATE TABLE IF NOT EXISTS _edges_call (
    caller_id   TEXT NOT NULL,
    callee_id   TEXT,
    callee_name TEXT NOT NULL,
    PRIMARY KEY (caller_id, callee_name)
);
CREATE INDEX IF NOT EXISTS _edges_call_callee ON _edges_call(callee_id);
CREATE INDEX IF NOT EXISTS _edges_call_name   ON _edges_call(callee_name);

CREATE TABLE IF NOT EXISTS _edges_import (
    source_id TEXT NOT NULL,   -- the importing file
    module    TEXT NOT NULL,   -- the imported module ('os', 'flex.core', '.state')
    name      TEXT,            -- the imported symbol (NULL for plain `import module`)
    PRIMARY KEY (source_id, module, name)
);
CREATE INDEX IF NOT EXISTS _edges_import_module ON _edges_import(module);
"""

_INSTANT_DDL = _FS_IDENTITY_DDL + _TYPES_DDL + _CALL_DDL

# Recursive query artifact (mirrors claude_code/stock/presets/delegation-tree.sql).
# Dormant at depth-1 (no _edges_tree rows); the seat for multi-level containers.
_SUBTREE_PRESET_SQL = (
    "WITH RECURSIVE sub(id, depth) AS ("
    "SELECT id, depth FROM _edges_tree WHERE parent_id = :root "
    "UNION ALL "
    "SELECT e.id, e.depth FROM _edges_tree e JOIN sub ON e.parent_id = sub.id) "
    "SELECT c.* FROM chunks c JOIN sub ON c.id = sub.id ORDER BY sub.depth, c.position"
)

# Call-graph presets — CodeGraph's callers/callees/impact as SQL (daemon-free).
_CALLERS_SQL = (
    "SELECT DISTINCT t.section_title AS caller, e.caller_id "
    "FROM _edges_call e JOIN _types_instant t ON e.caller_id = t.chunk_id "
    "WHERE e.callee_name = :symbol"
)
_CALLEES_SQL = (
    "SELECT DISTINCT e.callee_name AS callee, e.callee_id "
    "FROM _edges_call e JOIN _types_instant t ON e.caller_id = t.chunk_id "
    "WHERE t.section_title = :symbol"
)
_IMPACT_SQL = (
    "WITH RECURSIVE up(id) AS ("
    "SELECT caller_id FROM _edges_call WHERE callee_name = :symbol "
    "UNION "
    "SELECT e.caller_id FROM _edges_call e JOIN up ON e.callee_id = up.id) "
    "SELECT DISTINCT t.section_title AS affected FROM up JOIN _types_instant t ON up.id = t.chunk_id"
)


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
    _add_arg(parser, "--nest", action="store_true",
             help="Multi-level: build the heading tree (container > container > leaf) + _edges_tree")
    _add_arg(parser, "--config", action="store_true",
             help="Cascade: each file split by the nearest .flexchunk.json 'split' rule (per-folder rules)")


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


def _load_chunking_mode(name: str) -> str:
    """Read the stored split mode from the recipe ('nest' | 'config' | 'flat').
    Lets a bare --regen preserve the mode — the cell carries its own derivation."""
    from flex.core import get_meta, open_cell_readonly
    from flex.registry import resolve_cell

    cell_path = resolve_cell(name)
    if cell_path is None:
        return "flat"
    ro = open_cell_readonly(str(cell_path))
    try:
        raw = get_meta(ro, "chunking")
        if raw:
            try:
                return json.loads(raw).get("split_mode", "flat")
            except (ValueError, AttributeError):
                return "flat"
        return "flat"
    finally:
        ro.close()


# Noise dirs pruned during the walk — the .gitignore'd bulk (venv/build/caches/deps).
_WALK_EXCLUDE = {
    "__pycache__", "venv", ".venv", "env", "build", "dist", "node_modules",
    "site-packages", ".git", ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache",
    ".idea", ".vscode", "target", ".next", "coverage", "htmlcov", "_corpus",
}


def _walk_files(folder):
    """Yield (Path, text) for indexable files, pruning noise dirs (venv/build/caches/
    deps) and dot-dirs — the .gitignore'd bulk that should never be indexed. Uses
    os.walk so excluded trees are never descended into (fast on junk-heavy roots)."""
    import os
    from pathlib import Path  # noqa: F811
    exts = (".md", ".txt", ".py", ".js", ".ts", ".jsx", ".tsx",
            ".rs", ".go", ".yaml", ".yml", ".json", ".csv")
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _WALK_EXCLUDE and not d.startswith(".") and not d.endswith(".egg-info")
        )
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            if p.suffix not in exts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not text.strip():
                continue
            yield p, text


def _build_code_tree(abs_path: str, text: str) -> list[dict]:
    """Python AST tree: module ⊃ class ⊃ method/function, span-based.

    A node's content is its OWN region (signature + body up to its first nested def);
    nested defs are recursed as children — so a class node does not duplicate its
    methods' text (the code analogue of a heading-section excluding its sub-sections).
    A '(module)' leaf captures the module-level preamble (docstring/imports) before the
    first def. Falls back to flat chunking on a SyntaxError or a def-less file."""
    import ast as _ast
    from flex.sdk import _make_chunk_id

    try:
        tree = _ast.parse(text)
    except SyntaxError:
        return _build_nodes(abs_path, text, "flat")
    lines = text.splitlines()
    DEF = (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)
    top_defs = [n for n in _ast.iter_child_nodes(tree) if isinstance(n, DEF)]
    if not top_defs:
        return _build_nodes(abs_path, text, "flat")

    nodes: list[dict] = []

    def _own_call_names(node):
        """Bare-function calls in node's OWN scope (pruning nested def subtrees).
        Only `Name(...)` calls (`foo()`) — high-confidence internal-function edges.
        `x.method()` (Attribute) is dropped: name-only resolution can't tell
        `wal.append()` from `list.append()`, so method calls are container/db noise."""
        prune = {id(c) for c in _ast.iter_child_nodes(node) if isinstance(c, DEF)}
        names = []

        def _rec(n):
            for child in _ast.iter_child_nodes(n):
                if id(child) in prune:
                    continue
                if isinstance(child, _ast.Call) and isinstance(child.func, _ast.Name):
                    names.append(child.func.id)
                _rec(child)
        _rec(node)
        return names

    def _emit(node, depth, parent_id):
        kids = [c for c in _ast.iter_child_nodes(node) if isinstance(c, DEF)]
        start = node.lineno - 1
        end = (kids[0].lineno - 1) if kids else (node.end_lineno or node.lineno)
        own = "\n".join(lines[start:end]).rstrip() or \
            "\n".join(lines[node.lineno - 1:(node.end_lineno or node.lineno)])
        cid = _make_chunk_id(abs_path, len(nodes), own)
        nodes.append({"id": cid, "content": own, "section_title": node.name,
                      "position": len(nodes), "depth": depth, "container_id": parent_id,
                      "_calls": _own_call_names(node)})
        for c in kids:
            _emit(c, depth + 1, cid)

    preamble = "\n".join(lines[:top_defs[0].lineno - 1]).strip()
    if preamble:
        nodes.append({"id": _make_chunk_id(abs_path, len(nodes), preamble),
                      "content": preamble, "section_title": "(module)",
                      "position": len(nodes), "depth": 1, "container_id": abs_path})
    for node in top_defs:
        _emit(node, 1, abs_path)
    return nodes


def _build_tree(abs_path: str, text: str) -> list[dict]:
    """Build the node tree for one file.

    Markdown → full-depth heading tree (container > container > leaf) via a depth
    stack over split_sections(return_depth=True). Every heading-section is a content
    node (its body) AND a container (parent of deeper sections) — "container and
    chunk are the same node at different levels." container_id = the parent node's id
    (or the file path for top-level). Non-markdown → flat chunk_file_body under root.
    """
    from flex.compile.chunkers import chunk_file_body
    from flex.compile.markdown import normalize_headers, split_sections
    from flex.sdk import _make_chunk_id

    ext = abs_path.rsplit(".", 1)[-1].lower() if "." in abs_path else ""
    if ext == "py":
        return _build_code_tree(abs_path, text)
    nodes: list[dict] = []

    if ext == "md":
        sections = split_sections(normalize_headers(text), level=1, return_depth=True)
        stack: list[tuple[int, str]] = []  # (depth, node_id) for parent resolution
        for title, body, pos, depth in sections:
            cid = _make_chunk_id(abs_path, pos, body)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else abs_path
            nodes.append({"id": cid, "content": body, "section_title": title,
                          "position": pos, "depth": depth, "container_id": parent})
            stack.append((depth, cid))
        if not sections:  # no headings → whole file, one leaf under root
            nodes.append({"id": _make_chunk_id(abs_path, 0, text), "content": text,
                          "section_title": "", "position": 0, "depth": 1,
                          "container_id": abs_path})
    else:
        for i, part in enumerate(chunk_file_body(text, abs_path)):
            body = part.get("content") or part.get("body") or text
            nodes.append({"id": _make_chunk_id(abs_path, i, body), "content": body,
                          "section_title": part.get("title", "") or "",
                          "position": part.get("position", i), "depth": 1,
                          "container_id": abs_path})
    return nodes


_FLEXCHUNK = ".flexchunk.json"


def _nearest_split(file_path: str, cache: dict) -> str:
    """Cascade resolver: walk up from the file's dir; the nearest .flexchunk.json
    'split' wins. Default 'flat'. This is the per-folder declared-rule seam —
    any nested folder can drop a .flexchunk.json and live by its own rule."""
    from pathlib import Path  # noqa: F811
    d = Path(file_path).parent
    while True:
        if d not in cache:
            cj = d / _FLEXCHUNK
            sp = None
            if cj.is_file():
                try:
                    sp = json.loads(cj.read_text()).get("split")
                except Exception:
                    sp = None
            cache[d] = sp
        if cache[d]:
            return cache[d]
        if d.parent == d:
            return "flat"
        d = d.parent


def _build_nodes(abs_path: str, text: str, mode: str) -> list[dict]:
    """Build nodes under a declared split mode:
    'nest' (heading tree) | 'flat' (chunk_file_body, depth-1) | 'whole' (one leaf)."""
    from flex.compile.chunkers import chunk_file_body
    from flex.sdk import _make_chunk_id
    if mode == "whole":
        return [{"id": _make_chunk_id(abs_path, 0, text), "content": text,
                 "section_title": "", "position": 0, "depth": 1, "container_id": abs_path}]
    if mode == "nest":
        return _build_tree(abs_path, text)
    nodes = []
    for i, part in enumerate(chunk_file_body(text, abs_path)):
        body = part.get("content") or part.get("body") or text
        nodes.append({"id": _make_chunk_id(abs_path, i, body), "content": body,
                      "section_title": part.get("title", "") or "",
                      "position": part.get("position", i), "depth": 1,
                      "container_id": abs_path})
    return nodes


def run(args, console) -> None:
    """Compile the union of selections into one unlisted, no-embed cell (v5)."""
    from datetime import datetime, timezone
    from pathlib import Path

    from rich.panel import Panel
    from rich.text import Text

    from flex.core import set_meta
    from flex.registry import FLEX_HOME, register_cell
    from flex.sdk import _make_chunk_id, _walk_and_chunk, create, ingest, link, register, source

    nest = getattr(args, "nest", False)
    config_mode = getattr(args, "config", False)

    regen = getattr(args, "regen", None)
    name = (getattr(args, "name", None)
            or (regen if isinstance(regen, str) else None)
            or "fs")

    # ── resolve selections + recipe (the cell carries its own derivation) ──
    selections = _load_selections(name)
    # bare --regen (no explicit mode) → restore the recipe's stored split mode
    if regen is not None and not nest and not config_mode:
        stored = _load_chunking_mode(name)
        nest = stored == "nest"
        config_mode = stored == "config"
    tree = nest or config_mode  # both modes write _types_instant + the _edges_tree tree
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
    if config_mode:
        cfg_cache: dict = {}
        for root_str in selections:
            root = Path(root_str)
            for f, text in _walk_files(root):
                abs_path = str(f.resolve())
                if abs_path in by_file:
                    skipped_dupes += 1
                    continue
                mode = _nearest_split(abs_path, cfg_cache)
                by_file[abs_path] = _build_nodes(abs_path, text, mode)
    elif nest:
        for root_str in selections:
            root = Path(root_str)
            for f, text in _walk_files(root):
                abs_path = str(f.resolve())
                if abs_path in by_file:
                    skipped_dupes += 1
                    continue
                by_file[abs_path] = _build_tree(abs_path, text)
    else:
        for root_str in selections:
            root = Path(root_str)
            claimed_before = set(by_file)
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

    db = create(name, desc, cell_type="instant", db_path=db_path, schema=_INSTANT_DDL)

    for abs_path, file_chunks in by_file.items():
        source(db, abs_path, Path(abs_path).name)
        if tree:
            # nest/config: nodes already carry id/section_title/position/depth/container_id
            for ch in file_chunks:
                ch["content_hash"] = hashlib.sha256((ch.get("content") or "").encode("utf-8")).hexdigest()
            ingest(db, abs_path, file_chunks, types="_types_instant")
            for ch in file_chunks:  # container chain → _edges_tree (subtree preset reads this)
                link(db, ch["id"], ch["container_id"], relation="subsection", depth=ch["depth"])
        else:
            # depth-1: enrich the flat leaves chunk_file_body already produced. Does NOT
            # set 'id' or touch 'content' → chunk_id + _raw_chunks byte-identical to v4.
            for pos, ch in enumerate(file_chunks):
                ch["section_title"] = ch.get("title", "") or ""
                ch.setdefault("position", pos)
                ch["depth"] = 1
                ch["container_id"] = abs_path
                ch["content_hash"] = hashlib.sha256((ch.get("content") or "").encode("utf-8")).hexdigest()
            ingest(db, abs_path, file_chunks, types="_types_instant")

    # ── call graph (Python nest/config): resolve call sites vs the cell-wide symbol map ──
    name_to_ids: dict[str, list] = {}
    for _nodes in by_file.values():
        for n in _nodes:
            st = n.get("section_title")
            if st and not st.startswith("("):  # skip the (module) preamble leaf
                name_to_ids.setdefault(st, []).append(n["id"])
    call_rows = []
    for _nodes in by_file.values():
        for n in _nodes:
            for nm in n.get("_calls", ()):  # only Python def nodes carry _calls
                ids = name_to_ids.get(nm)
                callee = ids[0] if ids and len(ids) == 1 else None  # unique name → resolved
                call_rows.append((n["id"], callee, nm))
    if call_rows:
        db.executemany(
            "INSERT OR IGNORE INTO _edges_call (caller_id, callee_id, callee_name) VALUES (?, ?, ?)",
            call_rows,
        )

    # ── import graph (Python): file → imported module/symbol (codegraph's import edges) ──
    if tree:
        import ast as _ast2
        import_rows = []
        for abs_path in by_file:
            if not abs_path.endswith(".py"):
                continue
            try:
                _t = _ast2.parse(Path(abs_path).read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for nd in _ast2.walk(_t):
                if isinstance(nd, _ast2.Import):
                    for a in nd.names:
                        import_rows.append((abs_path, a.name, None))
                elif isinstance(nd, _ast2.ImportFrom):
                    mod = ("." * (nd.level or 0)) + (nd.module or "")
                    for a in nd.names:
                        import_rows.append((abs_path, mod, a.name))
        if import_rows:
            db.executemany(
                "INSERT OR IGNORE INTO _edges_import (source_id, module, name) VALUES (?, ?, ?)",
                import_rows,
            )

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

    # ── v5: ship the recursive subtree preset (the query artifact; dormant at depth-1) ──
    for _name, _desc, _sql in (
        ("subtree", "Recursive descendants of a container node (over _edges_tree).", _SUBTREE_PRESET_SQL),
        ("callers", "Who calls a symbol (call graph). @callers symbol=NAME", _CALLERS_SQL),
        ("callees", "What a symbol calls (call graph). @callees symbol=NAME", _CALLEES_SQL),
        ("impact", "Multi-hop callers — what's affected if a symbol changes. @impact symbol=NAME", _IMPACT_SQL),
    ):
        _params = "root" if _name == "subtree" else "symbol"
        db.execute(
            "INSERT OR REPLACE INTO _presets (name, description, params, sql) VALUES (?, ?, ?, ?)",
            (_name, _desc, _params, _sql),
        )

    # ── recipe stamp: the cell carries its own derivation ──
    set_meta(db, "selections", json.dumps(selections))
    _split_mode = "config" if config_mode else ("nest" if nest else "flat")
    set_meta(db, "chunking", json.dumps({"split_mode": _split_mode}))
    set_meta(db, "resolver", "node_tree@v1")
    set_meta(db, "profile", "instant")
    set_meta(db, "compiled_at", datetime.now(timezone.utc).isoformat())

    # embed() and graph() are deliberately absent, permanently.
    register(db, name, desc, cell_type="instant")
    register_cell(name, str(db_path), unlisted=True)
    db.commit()
    db.close()

    panel = Text()
    panel.append("Instant cell compiled (v5 recursive-node).\n\n", style="cyan")
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
    panel.append("Nodes       ", style="")
    panel.append("section_title · position · depth · container_id · content_hash → _types_instant\n", style="green")
    panel.append("Identity    ", style="")
    panel.append(f"{soma_status}\n", style="green")
    panel.append("Mode        ", style="")
    panel.append("no embeddings · no graph · unlisted · subtree preset shipped\n\n", style="yellow")
    panel.append("  flex core search --cell ", style="bold")
    panel.append(f"{name} ", style="bold green")
    panel.append('"@orient"\n', style="bold")
    panel.append("  grow:    flex init --module instant --path FOLDER\n", style="dim")
    panel.append("  regen:   flex init --module instant --regen", style="dim")
    panel.append(f"{'' if name == 'fs' else ' ' + name}\n", style="dim green")
    console.print(Panel(panel, padding=(1, 2), highlight=False))
