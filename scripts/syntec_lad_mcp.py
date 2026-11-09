#!/usr/bin/env python3
"""Launch the bundled Syntec ladder MCP server without installing the package."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from syntec_lad.mcp_server import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
