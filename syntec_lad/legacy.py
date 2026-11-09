"""Compatibility adapter for the original ``tools/lad_parser.py``.

The first reverse-engineering parser remains useful as an independent
cross-check and as a familiar CLI/API for existing scripts.  It is loaded by
path so the SDK continues to work when the repository root is not a Python
package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "lad_parser.py"
    if not path.is_file():
        raise ImportError(f"legacy parser not found: {path}")
    spec = importlib.util.spec_from_file_location("_syntec_legacy_lad_parser", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load legacy parser: {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their defining module through sys.modules while the
    # class body is being executed.  Register before exec_module so the legacy
    # parser can be loaded both as a script and through this compatibility
    # adapter.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


_MODULE: ModuleType | None = None


def module() -> ModuleType:
    global _MODULE
    if _MODULE is None:
        _MODULE = _load()
    return _MODULE


def parse_lad(
    path: Path | str,
    encoding: str = "big5",
    sidecar_path: Path | None = None,
) -> dict[str, Any]:
    return module().parse_lad(Path(path), encoding=encoding, sidecar_path=sidecar_path)


def parse_lc(path: Path | str) -> list[dict[str, str]]:
    return module().parse_lc(Path(path))


def render_disassembly(result: dict[str, Any]) -> str:
    return module().render_disassembly(result)


__all__ = ["module", "parse_lad", "parse_lc", "render_disassembly"]
