"""Install a sealed schema forest as one static native Flex SQLite cell."""

from __future__ import annotations

from pathlib import Path


CLI_NAME = "schema-forest"
MODULE_SUMMARY = "compile a sealed schema forest into provider-native SQLite"
MODULE = {
    "cell_type": "schema_forest",
    "maturity": "labs-source-cell",
    "description": (
        "Source-faithful schema forest: exact occurrences, schemas, families, "
        "failures, and generated native data tables from a sealed capture."
    ),
    "default_cell_name": "asset_world",
    "presets_from": ("schema_forest",),
    "instructions_from": ("schema_forest",),
    "query_examples": ("@orient",),
}


def _add_arg(parser, *flags, **kwargs) -> None:
    existing = {flag for action in parser._actions for flag in action.option_strings}
    if not any(flag in existing for flag in flags):
        parser.add_argument(*flags, **kwargs)


def register_args(parser) -> None:
    _add_arg(parser, "--name", default=None, help="Durable cell name")
    _add_arg(parser, "--capture", required=False, help="Sealed schema-forest capture")


def run(args, console) -> None:
    from .compile.worker import build_cell, register_cell_path

    capture_value = getattr(args, "capture", None)
    if not capture_value:
        raise SystemExit("--capture is required for the schema-forest module")
    capture = Path(capture_value).resolve()
    name = getattr(args, "name", None) or MODULE["default_cell_name"]
    target = capture / ".derived" / f"{name}.db"
    receipt_path = (
        capture / ".receipts" / f"{name}-schema-forest-native-flex-build.json"
    )
    receipt = build_cell(capture, target, receipt_path=receipt_path)
    register_cell_path(name, target, description=MODULE["description"])
    console.print(f"  cell        [bold green]{name}[/bold green]")
    console.print(
        f"  occurrences [green]{receipt['counts']['asset_occurrences']}[/green]"
    )
    console.print(f"  schemas     [green]{receipt['counts']['asset_schemas']}[/green]")
    console.print(f"  failures    [green]{receipt['counts']['asset_failures']}[/green]")
    console.print(
        f"  native      [green]{receipt['counts']['asset_native_tables']}[/green]"
    )
    console.print(f"  database    {target}")
    console.print(f"  receipt     {receipt_path}")
    console.print(f"  flex search --cell {name} \"@orient\"")
