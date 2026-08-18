"""Install a sealed XOLogic capture as one native Flex SQLite cell."""

from __future__ import annotations

from pathlib import Path

CLI_NAME = "xologic"
MODULE_SUMMARY = "compile a sealed XOLogic catalogue capture into native SQLite"
MODULE = {
    "cell_type": "xologic",
    "maturity": "labs-source-cell",
    "description": "Source-faithful XOLogic catalogue: source pointers, native occurrences, and SQL projections.",
    "default_cell_name": "xologic_catalogue",
    "refresh_module": "flex.modules.xologic.compile.refresh",
    "refresh_interval": 6 * 60 * 60,
    "presets_from": ("xologic",),
    "instructions_from": ("xologic",),
    "query_examples": ("@orient",),
}


def _add_arg(parser, *flags, **kwargs):
    existing = {flag for action in parser._actions for flag in action.option_strings}
    if not any(flag in existing for flag in flags):
        parser.add_argument(*flags, **kwargs)


def register_args(parser) -> None:
    _add_arg(parser, "--name", default=None, help="Durable cell name, e.g. bes_lighting_xologic")
    _add_arg(parser, "--capture", required=False, help="Sealed XOLogic capture directory")
    _add_arg(parser, "--store-name", default=None, help="Human-readable store name")


def run(args, console) -> None:
    from flex.registry import CELLS_DIR
    from .compile.worker import build_cell, register_cell_path

    capture = getattr(args, "capture", None)
    if not capture:
        raise SystemExit("--capture is required for the xologic module")
    name = getattr(args, "name", None) or MODULE["default_cell_name"]
    CELLS_DIR.mkdir(parents=True, exist_ok=True)
    path = CELLS_DIR / f"{name}.db"
    receipt = build_cell(Path(capture), path, store_name=getattr(args, "store_name", None))
    register_cell_path(name, path, description=MODULE["description"])
    console.print(f"  cell                [bold green]{name}[/bold green]")
    console.print(f"  products            [green]{receipt['counts']['products']}[/green]")
    console.print(f"  source observations [green]{receipt['counts']['xologic_source_observations']}[/green]")
    console.print(f"  flex search --cell {name} \"@orient\"")
