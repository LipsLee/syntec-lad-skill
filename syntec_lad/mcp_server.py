"""Minimal MCP stdio server for the Syntec ladder SDK.

The server is intentionally small and dependency-free. It exposes the native
inspection, address-lookup, build, patch, validate, and diff operations to
MCP clients such as WorkBuddy, Claude Desktop, Cursor, and other agents.

Protocol: JSON-RPC 2.0 over newline-delimited stdio, MCP 2024-11-05.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .address_db import default_address_db_path, lookup_address
from .inspection import inspect_lad

SKILL_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = SKILL_ROOT / "references"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "inspect_lad",
        "description": (
            "Inspect a native Syntec .lad file: programs, comments, record "
            "ranges, hashes, network candidates, and integrity issues."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "absolute or relative .lad path"},
                "encoding": {
                    "type": "string",
                    "default": "auto",
                    "description": "auto, big5, gb2312, or gb18030",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "lookup_address",
        "description": (
            "Look up a known C/S/R address or search the bundled 602-address "
            "engineering index by Chinese/English keyword."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "C6, C006, R600.1, or a keyword"},
                "limit": {"type": "integer", "default": 20, "minimum": 1},
            },
            "required": ["query"],
        },
    },
    {
        "name": "build_plan",
        "description": (
            "Build a new .lad from a reviewed syntec-lad-plan/v1 JSON file. "
            "Native LadDLL validation is enabled by default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "path to the plan JSON"},
                "output": {"type": "string", "description": "new .lad output path"},
                "no_native": {
                    "type": "boolean",
                    "default": False,
                    "description": "skip LadDLL only for a parser-only diagnostic",
                },
            },
            "required": ["plan", "output"],
        },
    },
    {
        "name": "patch_lad",
        "description": (
            "Apply a hash-pinned syntec-lad-change/v1 document to an existing "
            ".lad and write a new output file. Native validation is enabled by default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lad": {"type": "string", "description": "source .lad path"},
                "change": {"type": "string", "description": "change JSON path"},
                "output": {"type": "string", "description": "new output .lad path"},
                "no_native": {
                    "type": "boolean",
                    "default": False,
                    "description": "skip LadDLL only for a parser-only diagnostic",
                },
            },
            "required": ["lad", "change", "output"],
        },
    },
    {
        "name": "validate_lad",
        "description": (
            "Validate a .lad with Python readback and, by default, the native "
            "32-bit LadDLL syntax checker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lad": {"type": "string", "description": ".lad path"},
                "no_native": {
                    "type": "boolean",
                    "default": False,
                    "description": "skip LadDLL only for a parser-only diagnostic",
                },
            },
            "required": ["lad"],
        },
    },
    {
        "name": "diff_lad",
        "description": "Compare two .lad files at record and instruction-cell granularity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "left": {"type": "string"},
                "right": {"type": "string"},
                "context": {"type": "integer", "default": 8, "minimum": 0},
            },
            "required": ["left", "right"],
        },
    },
    {
        "name": "read_reference",
        "description": (
            "Read a bundled knowledge reference by relative path, for example "
            "panming/patterns.md or knowledge-map.md."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "relative path under references/"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_references",
        "description": "List the bundled engineering knowledge reference files.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "explain_capabilities",
        "description": "Explain the tool workflow, safety boundary, and native validation gates.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _run_cli(args: list[str], *, fail_on_error: bool = True) -> dict[str, Any]:
    command = [sys.executable, "-m", "syntec_lad", *args]
    cwd = str(SKILL_ROOT) if (SKILL_ROOT / "syntec_lad").is_dir() else None
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    if fail_on_error and result.returncode != 0:
        detail = payload["stderr"] or payload["stdout"] or "no output"
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return payload


def _tool_inspect(args: dict[str, Any]) -> dict[str, Any]:
    return inspect_lad(
        args["path"],
        encoding=str(args.get("encoding") or "auto"),
    )


def _tool_lookup(args: dict[str, Any]) -> dict[str, Any]:
    hits = lookup_address(
        str(args["query"]),
        limit=int(args.get("limit") or 20),
    )
    return {
        "query": args["query"],
        "count": len(hits),
        "hits": hits,
        "database": str(default_address_db_path()),
    }


def _tool_build(args: dict[str, Any]) -> dict[str, Any]:
    command = ["build", str(args["plan"]), str(args["output"])]
    if args.get("no_native"):
        command.append("--no-native")
    return _run_cli(command)


def _tool_patch(args: dict[str, Any]) -> dict[str, Any]:
    command = [
        "patch",
        str(args["lad"]),
        str(args["change"]),
        str(args["output"]),
    ]
    if args.get("no_native"):
        command.append("--no-native")
    return _run_cli(command)


def _tool_validate(args: dict[str, Any]) -> dict[str, Any]:
    command = ["validate", str(args["lad"])]
    if args.get("no_native"):
        command.append("--no-native")
    return _run_cli(command)


def _tool_diff(args: dict[str, Any]) -> dict[str, Any]:
    command = [
        "diff",
        str(args["left"]),
        str(args["right"]),
        "--context",
        str(int(args.get("context") or 8)),
    ]
    return _run_cli(command, fail_on_error=False)


def _resolve_reference(name: str) -> Path:
    root = REFERENCE_ROOT.resolve()
    candidate = (root / name).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("reference path escapes references/")
    if not candidate.is_file():
        raise ValueError(f"reference not found: {name}")
    if candidate.suffix.lower() not in {".md", ".json", ".csv", ".txt"}:
        raise ValueError("only text reference files can be read")
    return candidate


def _tool_read_reference(args: dict[str, Any]) -> str:
    path = _resolve_reference(str(args["name"]))
    return path.read_text(encoding="utf-8-sig")


def _tool_list_references(_: dict[str, Any]) -> list[str]:
    root = REFERENCE_ROOT.resolve()
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() in {".md", ".json", ".csv", ".txt"}
    )


def _tool_explain(_: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Syntec Ladder SDK MCP adapter",
            "",
            "Workflow:",
            "1. inspect_lad on the native source.",
            "2. Read the relevant reference via read_reference.",
            "3. Resolve every missing I/O, register, timer, counter, preset, and interlock value.",
            "4. Create a reviewed syntec-lad-plan/v1 or syntec-lad-change/v1 JSON document.",
            "5. build_plan or patch_lad to a new output path.",
            "6. validate_lad and diff_lad.",
            "",
            "Safety:",
            "- Native .lad is the only writable source format.",
            "- Knowledge examples do not supply machine-specific addresses.",
            "- Final delivery requires parser readback and native LadDLL success.",
            "- This server never clicks the editor and never uploads to a CNC.",
            "",
            f"Address database: {default_address_db_path()}",
        ]
    )


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "inspect_lad": _tool_inspect,
    "lookup_address": _tool_lookup,
    "build_plan": _tool_build,
    "patch_lad": _tool_patch,
    "validate_lad": _tool_validate,
    "diff_lad": _tool_diff,
    "read_reference": _tool_read_reference,
    "list_references": _tool_list_references,
    "explain_capabilities": _tool_explain,
}


def _error(code: int, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _result(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else _json_text(value)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }


def _dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "syntec-lad", "version": "0.2.2"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            raise KeyError(f"unknown tool: {name}")
        return _result(handler(arguments))
    raise KeyError(f"unsupported method: {method}")


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = str(request.get("method") or "")
    params = request.get("params") or {}
    if request_id is None:
        return None
    try:
        result = _dispatch(method, params)
    except KeyError as exc:
        return {"id": request_id, "error": _error(-32601, str(exc))}
    except Exception as exc:  # tool errors are reported inside the result
        if method == "tools/call":
            return {
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"tool error: {exc}"}],
                    "isError": True,
                },
            }
        return {"id": request_id, "error": _error(-32603, str(exc))}
    return {"id": request_id, "result": result}


def _force_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _force_utf8_stdio()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        response = _handle_request(request)
        if response is not None:
            sys.stdout.write(
                json.dumps(
                    {"jsonrpc": "2.0", **response},
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
