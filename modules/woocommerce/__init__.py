"""Lossless public WooCommerce Store API source module for Flex Labs.

The module deliberately captures only the public ``wc/store/v1/products``
contract. Its native Woo SQLite tables, exact Store API page bytes, and raw
product JSON are the authority.
"""

from .install import MODULE

__all__ = ["MODULE"]
