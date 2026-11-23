"""Locate the ladder/parameter/alarm files inside a Syntec machine backup.

Syntec deliveries arrive in a few fixed shapes. Knowing them means the caller
can point at one directory instead of eight individual files:

MB backup (``M7R2337_2026xxxx_MBL_xxxx``) - the backup内容 sits at the root::

    Ladder/cnc.lad                    Ladder/AlarmPLC_CHS.xml
    ParameterExt/paramext.pxt          ParameterExt/ParamExt_CHS.xml
    ParameterExt/ParamExt_RBit_CHS.xml  IOMap/IoMap_Customer.xml

SB backup (``M6R5123_2026xxxx_SB_xxxx``) - mirrors the C drive::

    DiskC/OpenCNC/Ladder/CNC.LAD      DiskC/OpenCNC/Res/paramext.pxt
    DiskC/OCRes/CHS/String/ParamExt_CHS.Xml
    DiskC/OCRes/CHS/String/AlarmPLC_CHS.Xml

Package installers (``package_xxxx/PackageDir/DiskC/...``) use the SB shape.
Any other directory falls back to a bounded filename search.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import LadValidationError

DELIVERY_SCHEMA = "syntec-lad-delivery/v1"
LANGUAGES = ("CHS", "CHT", "COM")

_SKIP_DIRS = {
    "bin", "windows", "firmware", "system32", "syswow64", "temp", "tmp",
    "__pycache__", ".git", "node_modules",
}
_MAX_DEPTH = 7


def _exists_ci(parent: Path, name: str) -> Path | None:
    if not parent.is_dir():
        return None
    wanted = name.casefold()
    for child in parent.iterdir():
        if child.name.casefold() == wanted:
            return child
    return None


def _join_ci(base: Path, *parts: str) -> Path | None:
    current = base
    for part in parts:
        found = _exists_ci(current, part)
        if found is None:
            return None
        current = found
    return current


def _walk_files(root: Path, max_depth: int = _MAX_DEPTH) -> list[Path]:
    files: list[Path] = []
    root = root.resolve()
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) > max_depth:
            continue
        if any(part.casefold() in _SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if path.is_file():
            files.append(path)
    return files


def _pick(files: list[Path], pattern: str, *, prefer: str | None = None) -> Path | None:
    regex = re.compile(pattern, re.IGNORECASE)
    matches = [path for path in files if regex.search(path.name)]
    if not matches:
        return None
    if prefer:
        preferred = [path for path in matches if prefer.casefold() in path.as_posix().casefold()]
        if preferred:
            matches = preferred
    matches.sort(key=lambda path: (len(path.as_posix()), path.as_posix()))
    return matches[0]


def _language_map(files: list[Path], stem: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for language in LANGUAGES:
        regex = re.compile(rf"^{stem}_{language}\.(xml)$", re.IGNORECASE)
        matches = [path for path in files if regex.match(path.name)]
        if not matches:
            continue
        # Prefer the Ladder/ or OCRes/<lang>/String copy over stray duplicates.
        matches.sort(key=lambda path: (len(path.as_posix()), path.as_posix()))
        result[language] = matches[0]
    return result


def detect_layout(root: Path) -> str:
    if _join_ci(root, "Ladder") is not None and _join_ci(root, "ParameterExt") is not None:
        return "mb"
    if _join_ci(root, "DiskC") is not None:
        return "sb"
    if _join_ci(root, "PackageDir") is not None:
        return "package"
    return "plain"


def discover(root: str | Path) -> dict[str, Any]:
    """Find the ladder, parameter and alarm artefacts under ``root``."""

    base = Path(root)
    if not base.exists():
        raise LadValidationError(f"delivery path does not exist: {base}")
    if base.is_file():
        if base.suffix.casefold() != ".lad":
            raise LadValidationError(
                f"delivery path must be a .lad file or a backup directory: {base}"
            )
        return {
            "schema": DELIVERY_SCHEMA,
            "root": str(base.parent.resolve()),
            "layout": "single-file",
            "files": {"lad": str(base.resolve())},
            "missing": ["pxt", "param_xml", "alarm_xml", "io"],
        }

    base = base.resolve()
    layout = detect_layout(base)
    files = _walk_files(base)

    lad: Path | None = None
    pxt: Path | None = None
    parameter_dir_hint = "ParameterExt"

    if layout in {"sb", "package"}:
        disk = _join_ci(base, "DiskC") or base
        lad = (
            _join_ci(disk, "OpenCNC", "Ladder", "CNC.LAD")
            or _join_ci(disk, "OpenCNC", "Ladder", "cnc.lad")
            or _join_ci(disk, "Ladder", "cnc.lad")
        )
        pxt = (
            _join_ci(disk, "OpenCNC", "Res", "paramext.pxt")
            or _join_ci(disk, "OpenCNC", "ParameterExt", "paramext.pxt")
        )
        parameter_dir_hint = "OCRes"
    elif layout == "mb":
        ladder_dir = _join_ci(base, "Ladder")
        if ladder_dir is not None:
            lad = (
                _exists_ci(ladder_dir, "cnc.lad")
                or _exists_ci(ladder_dir, "CNC.LAD")
                or next(
                    (item for item in sorted(ladder_dir.glob("*.lad"))),
                    None,
                )
            )
        pxt = _join_ci(base, "ParameterExt", "paramext.pxt")

    if lad is None:
        lad = _pick(files, r"^cnc\.lad$") or _pick(files, r"\.lad$")
    if pxt is None:
        pxt = _pick(files, r"^paramext\.pxt$", prefer=parameter_dir_hint)

    param_xml = _language_map(files, "ParamExt")
    alarm_xml = _language_map(files, "AlarmPLC")
    def _io_related(path: Path) -> bool:
        lowered = path.as_posix().casefold()
        if "oplog" in lowered or "backuponly" in lowered or "functionlog" in lowered:
            return False
        if "iomap" in lowered:
            return True
        return bool(re.search(r"(^|[/\\])io[/\\]", lowered))

    point_tables = [
        path
        for path in files
        if path.suffix.casefold() in {".dat", ".csv", ".txt"} and _io_related(path)
    ]
    port_maps = [
        path
        for path in files
        if path.suffix.casefold() == ".xml" and _io_related(path)
    ]
    for group in (point_tables, port_maps):
        group.sort(
            key=lambda path: (
                0 if "customer" in path.name.casefold() else 1,
                len(path.as_posix()),
            )
        )
    io_files = point_tables + port_maps

    files_map: dict[str, Any] = {
        "lad": str(lad) if lad else None,
        "pxt": str(pxt) if pxt else None,
        "param_xml": {k: str(v) for k, v in param_xml.items()},
        "alarm_xml": {k: str(v) for k, v in alarm_xml.items()},
        "io": [str(path) for path in io_files[:3]],
        "rbit_xml": {
            language: str(path)
            for language, path in _language_map(files, "ParamExt_RBit").items()
        },
    }

    missing = [
        key
        for key in ("lad", "pxt", "param_xml", "alarm_xml", "io")
        if not files_map.get(key)
    ]
    return {
        "schema": DELIVERY_SCHEMA,
        "root": str(base),
        "layout": layout,
        "files": files_map,
        "missing": missing,
    }


def resolve_inputs(
    *,
    lad: str | None = None,
    delivery: str | None = None,
    pxt: str | None = None,
    param_xml: list[str] | None = None,
    alarm_xml: str | None = None,
    io: str | None = None,
) -> dict[str, Any]:
    """Merge explicit paths with the ones discovered from a backup directory."""

    discovered: dict[str, Any] = {}
    if delivery:
        discovered = discover(delivery)
        files = discovered["files"]
        lad = lad or files.get("lad")
        pxt = pxt or files.get("pxt")
        if not param_xml:
            languages = files.get("param_xml") or {}
            param_xml = [languages[key] for key in LANGUAGES if key in languages]
        alarm_xml = alarm_xml or (files.get("alarm_xml") or {}).get("CHS")
        if not io:
            candidates = files.get("io") or []
            io = candidates[0] if candidates else None

    if not lad:
        raise LadValidationError(
            "no ladder file: pass a .lad path or --delivery pointing at a backup directory"
        )
    return {
        "lad": lad,
        "pxt": pxt,
        "param_xml": param_xml or [],
        "alarm_xml": alarm_xml,
        "io": io,
        "delivery": discovered,
    }


def render_discovery(result: dict[str, Any]) -> str:
    files = result["files"]
    lines = [
        "SYNTEC delivery discovery",
        f"Root   : {result['root']}",
        f"Layout : {result['layout']}",
        f"Ladder : {files.get('lad') or '(not found)'}",
        f"Pxt    : {files.get('pxt') or '(not found)'}",
    ]
    param_xml = files.get("param_xml") or {}
    lines.append("Param  : " + (", ".join(f"{k}={v}" for k, v in param_xml.items()) or "(not found)"))
    alarm_xml = files.get("alarm_xml") or {}
    lines.append("Alarm  : " + (", ".join(f"{k}={v}" for k, v in alarm_xml.items()) or "(not found)"))
    io_files = files.get("io") or []
    lines.append("IO     : " + (", ".join(io_files) or "(not found)"))
    if result["missing"]:
        lines.append("Missing: " + ", ".join(result["missing"]))
    return "\n".join(lines) + "\n"
