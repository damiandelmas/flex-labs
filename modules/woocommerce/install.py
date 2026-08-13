"""WooCommerce install hook.

Installs a public no-auth WooCommerce Store API source cell. The module captures
`/wp-json/wc/store/v1/products` exactly, stores raw response bytes in SQLite, and
exposes deterministic catalogue views over the raw layer.
"""

from __future__ import annotations

import sys


CLI_NAME = "woocommerce"
MODULE_SUMMARY = "index a public WooCommerce Store API catalogue as raw SQLite"

MODULE = {
    "cell_type": "woocommerce",
    "maturity": "public-source-cell",
    "release_posture": "public",
    "license_intent": "MIT-compatible external module",
    "auth": "none",
    "description": "Raw public WooCommerce Store API catalogue. Exact Store API JSON/bytes remain authoritative.",
    "default_cell_name": "woocommerce",
    "refresh_module": "flex.modules.woocommerce.compile.refresh",
    "refresh_interval": 6 * 60 * 60,
    "views_from": ("woocommerce",),
    "presets_from": ("woocommerce",),
    "query_examples": ("@orient", "@scope", "@products", "@categories"),
}


def _add_arg(parser, *flags, **kwargs) -> None:
    existing = {opt for action in parser._actions for opt in action.option_strings}
    if not any(flag in existing for flag in flags):
        parser.add_argument(*flags, **kwargs)


def register_args(parser) -> None:
    _add_arg(parser, "--name", default=None, help="WooCommerce cell name (default: woocommerce)")
    _add_arg(parser, "--store-url", "--url", dest="store_url", default=None,
             help="Public WooCommerce storefront URL")
    _add_arg(parser, "--per-page", type=int, default=50,
             help="Store API products per page, 1-100 (default: 50)")
    _add_arg(parser, "--retries", type=int, default=2,
             help="Retries for transient Store API failures (default: 2)")
    _add_arg(parser, "--price-policy", choices=("public", "login_required", "hidden"),
             default="public", help="whether query surfaces may expose Store API prices")


def run(args, console) -> None:
    """Install and index a WooCommerce Store API cell through the module worker."""
    from rich.panel import Panel
    from rich.text import Text

    from flex.modules.woocommerce.compile import worker

    store_url = getattr(args, "store_url", None)
    if not store_url:
        raise SystemExit("--store-url is required for the woocommerce module")

    cell_name = getattr(args, "name", None) or MODULE["default_cell_name"]
    per_page = int(getattr(args, "per_page", 50))
    retries = int(getattr(args, "retries", 2))
    price_policy = str(getattr(args, "price_policy", "public"))

    console.print(f"  cell                [bold]{cell_name}[/bold]")
    console.print(f"  store               {store_url.rstrip('/')}")
    console.print("  auth                [green]none[/green]")
    console.print(f"  per page            {per_page}")
    console.print(f"  price policy        {price_policy}")
    console.print()

    argv = [
        "woocommerce-worker",
        "--cell", cell_name,
        "--url", store_url,
        "--per-page", str(per_page),
        "--retries", str(retries),
        "--price-policy", price_policy,
        "--description", MODULE["description"],
        "--refresh-interval", str(MODULE["refresh_interval"]),
    ]
    original_argv = sys.argv
    try:
        sys.argv = argv
        worker.main()
    finally:
        sys.argv = original_argv

    panel = Text()
    panel.append("WooCommerce cell ready.\n\n", style="cyan")
    panel.append("Cell                  ", style="")
    panel.append(f"{cell_name}\n", style="green")
    panel.append("Store                 ", style="")
    panel.append(store_url.rstrip("/"), style="green")
    panel.append("\n\n")
    panel.append("  flex core search --cell ", style="bold")
    panel.append(f"{cell_name} ", style="bold green")
    panel.append('"@orient"\n', style="bold")
    panel.append("  flex core search --cell ", style="bold")
    panel.append(f"{cell_name} ", style="bold green")
    panel.append('"@products"\n', style="bold")
    panel.append("  flex refresh --cells ", style="bold")
    panel.append(f"{cell_name}\n", style="bold green")
    console.print(Panel(panel, padding=(1, 2), highlight=False))
