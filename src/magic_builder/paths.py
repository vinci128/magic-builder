"""Filesystem locations shared across the package.

The cache defaults to `.cache/` under the current working directory, which is
where every entry point (CLI, web server, MCP server, Render build) runs from.
Set MAGIC_BUILDER_CACHE to put it somewhere else.
"""

import os
from pathlib import Path

CACHE_ROOT = Path(os.environ.get("MAGIC_BUILDER_CACHE", ".cache"))
