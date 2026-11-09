"""Python SDK for Syntec LadEditor .lad ladder files.

The public surface intentionally separates three concerns:

* :class:`LadDocument` reads and writes the native 40-byte record container.
* :func:`compile_plan` turns a reviewed ``syntec-lad-plan/v1`` document into
  records without requiring the GUI.
* :func:`validate_native` checks compatibility with the real 32-bit LadDLL.
"""

from .address_db import (
    default_address_db_path,
    load_address_db,
    lookup_address,
)
from .compiler import build_plan, compile_networks, compile_plan
from .errors import (
    LadError,
    LadFormatError,
    LadPatchError,
    LadUnsupportedError,
    LadValidationError,
    NativeValidationError,
)
from .inspection import inspect_lad
from .model import LadDocument
from .native import (
    find_editor,
    find_lad_dll,
    open_in_editor,
    validate_document,
    validate_native,
)
from .patch import (
    CHANGE_SCHEMA,
    PatchResult,
    apply_change_plan,
    apply_change_plan_file,
)
from .remap import (
    CommentRemapChange,
    CommentRemapResult,
    RemapChange,
    RemapResult,
    remap_plc_bits,
    remap_variable_comment_addresses,
    remap_variable_comments,
)

__version__ = "0.2.1"

__all__ = [
    "CHANGE_SCHEMA",
    "CommentRemapChange",
    "CommentRemapResult",
    "LadDocument",
    "LadError",
    "LadFormatError",
    "LadPatchError",
    "LadUnsupportedError",
    "LadValidationError",
    "NativeValidationError",
    "PatchResult",
    "RemapChange",
    "RemapResult",
    "apply_change_plan",
    "apply_change_plan_file",
    "build_plan",
    "compile_networks",
    "compile_plan",
    "default_address_db_path",
    "find_editor",
    "find_lad_dll",
    "inspect_lad",
    "load_address_db",
    "lookup_address",
    "open_in_editor",
    "remap_plc_bits",
    "remap_variable_comment_addresses",
    "remap_variable_comments",
    "validate_document",
    "validate_native",
]
