"""Command-line interface for the Syntec ladder SDK."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .compiler import compile_plan
from .errors import LadError, LadValidationError, NativeValidationError
from .inspection import inspect_document, inspect_lad, render_inspection
from .model import LadDocument
from .native import open_in_editor, validate_document, validate_native
from .patch import apply_change_plan, PatchResult
from .remap import remap_plc_bits


def _json_load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise LadValidationError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LadValidationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LadValidationError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path | None, value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _save_document(
    document: LadDocument,
    target: Path,
    *,
    in_place: bool,
    overwrite: bool = False,
    validate: bool,
    dll: str | None,
    checker: str | None,
) -> dict[str, Any]:
    if target.exists() and not (in_place or overwrite):
        raise LadValidationError(
            f"output exists: {target}; use an explicit new path or --in-place"
        )
    if in_place and not target.exists():
        raise LadValidationError(f"--in-place target does not exist: {target}")
    native_result: dict[str, Any] | None = None
    if validate:
        native_result = validate_document(
            document,
            dll_path=dll,
            checker_path=checker,
        )
    expected_bytes = document.to_bytes()
    saved = document.save(
        target,
        overwrite=in_place or overwrite,
        in_place=in_place,
    )
    try:
        readback = LadDocument.load(target)
        if readback.to_bytes() != expected_bytes:
            raise LadValidationError(
                f"parser readback mismatch after writing {target}"
            )
    except Exception:
        _rollback_saved_file(saved, target)
        raise
    saved["parser"] = {
        "passed": True,
        "records": len(readback.records),
        "sha256": readback.sha256,
        "encoding": readback.encoding,
    }
    saved["native"] = native_result
    return saved


def _rollback_saved_file(saved: dict[str, Any], target: Path) -> None:
    backup = saved.get("backup")
    if isinstance(backup, str) and Path(backup).is_file():
        shutil.copy2(backup, target)
    else:
        try:
            target.unlink()
        except OSError:
            pass
    sidecar = saved.get("sidecar")
    sidecar_backup = saved.get("sidecar_backup")
    if isinstance(sidecar, str) and isinstance(sidecar_backup, str):
        backup_path = Path(sidecar_backup)
        if backup_path.is_file():
            shutil.copy2(backup_path, Path(sidecar))


def _print_save_metadata(saved: dict[str, Any]) -> None:
    if saved.get("backup"):
        print(f"backup: {saved['backup']}")
    if saved.get("sidecar"):
        print(
            f"sidecar removed: {saved['sidecar']}  "
            f"backup={saved.get('sidecar_backup')}"
        )


def _cmd_inspect(args: argparse.Namespace) -> int:
    result = inspect_lad(args.lad, encoding=args.encoding)
    output = Path(args.output) if args.output else None
    if output is not None and output.resolve() == Path(args.lad).resolve():
        raise LadValidationError("inspect --output must not overwrite the input ladder")
    if args.json:
        _write_json(output, result)
    else:
        text = render_inspection(result, preview_rows=args.rows)
        if output is None:
            sys.stdout.write(text)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    plan = _json_load(Path(args.plan))
    if args.encoding:
        plan["encoding"] = args.encoding
    document = compile_plan(plan)
    issues = inspect_document(document)["integrity"]["issues"]
    if issues:
        raise LadValidationError(
            "generated plan failed parser inspection: " + "; ".join(issues)
        )
    target = Path(args.output).resolve()
    result = _save_document(
        document,
        target,
        in_place=args.in_place,
        validate=not args.no_native,
        dll=args.dll,
        checker=args.checker,
    )
    print(
        f"built {target}  records={len(document.records)}  "
        f"size={result['size']}  sha256={result['sha256']}"
    )
    print(
        f"parser readback: PASS  records={result['parser']['records']}  "
        f"encoding={result['parser']['encoding']}"
    )
    if result.get("native"):
        print(
            "native syntax: PASS  "
            f"error_count={result['native']['error_count']}"
        )
    _print_save_metadata(result)
    if args.open:
        open_in_editor(target, editor_path=args.editor)
        print(f"opened in LadEditor: {target}")
    return 0


def _cmd_patch(args: argparse.Namespace) -> int:
    source_path = Path(args.lad).resolve()
    change = _json_load(Path(args.change))
    document = LadDocument.load(source_path)
    result = apply_change_plan(document, change, return_result=True)
    assert isinstance(result, PatchResult)
    target = Path(args.output).resolve() if args.output else source_path
    if not args.in_place and target == source_path:
        raise LadValidationError(
            "patching the source path requires --in-place or a different output path"
        )
    saved = _save_document(
        result.document,
        target,
        in_place=args.in_place,
        validate=not args.no_native,
        dll=args.dll,
        checker=args.checker,
    )
    saved["before_sha256"] = result.before_sha256
    saved["after_sha256"] = result.after_sha256
    print(
        f"patched {source_path} -> {target}  "
        f"records={len(result.document.records)}  "
        f"before={result.before_sha256[:16]}  after={result.after_sha256[:16]}"
    )
    print(
        f"parser readback: PASS  records={saved['parser']['records']}  "
        f"encoding={saved['parser']['encoding']}"
    )
    if saved.get("native"):
        print("native syntax: PASS  error_count=0")
    _print_save_metadata(saved)
    if args.open:
        open_in_editor(target, editor_path=args.editor)
        print(f"opened in LadEditor: {target}")
    return 0


def _parse_map_entries(entries: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise LadValidationError(
                f"invalid --map entry {entry!r}; expected SOURCE=TARGET"
            )
        source, target = entry.split("=", 1)
        source = source.strip()
        target = target.strip()
        if not source or not target:
            raise LadValidationError(
                f"invalid --map entry {entry!r}; expected SOURCE=TARGET"
            )
        mapping[source] = target
    return mapping


def _load_mapping_file(path: Path) -> dict[str, str]:
    value = _json_load(path)
    if "mapping" in value:
        value = value["mapping"]
    if not isinstance(value, dict):
        raise LadValidationError(f"mapping JSON must be an object: {path}")
    result: dict[str, str] = {}
    for source, target in value.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise LadValidationError(
                f"mapping keys and values must be strings: {path}"
            )
        result[source] = target
    return result


def _cmd_remap(args: argparse.Namespace) -> int:
    source_path = Path(args.lad).resolve()
    document = LadDocument.load(source_path)
    mapping: dict[str, str] = {}
    if args.mapping_file:
        mapping.update(_load_mapping_file(Path(args.mapping_file)))
    mapping.update(_parse_map_entries(args.map))
    if not mapping:
        raise LadValidationError("remap requires --map and/or --mapping-file")

    result = remap_plc_bits(
        document,
        mapping,
        expected_sha256=args.expected_sha256,
        require_all_sources=args.require_all_sources,
    )
    issues = inspect_document(result.document)["integrity"]["issues"]
    if issues:
        raise LadValidationError(
            "remapped document failed parser inspection: " + "; ".join(issues)
        )

    target = Path(args.output).resolve() if args.output else source_path
    if target == source_path and not args.in_place:
        raise LadValidationError(
            "remapping the source path requires --in-place or a different output path"
        )
    saved = _save_document(
        result.document,
        target,
        in_place=args.in_place,
        overwrite=args.overwrite,
        validate=not args.no_native,
        dll=args.dll,
        checker=args.checker,
    )
    payload = {
        "source": str(source_path),
        "output": str(target),
        "before_sha256": result.before_sha256,
        "after_sha256": result.after_sha256,
        "mapping": dict(result.mapping),
        "source_counts": result.source_counts,
        "unmatched_sources": list(result.unmatched_sources),
        "changed_records": result.changed_records,
        "changed_operands": len(result.changes),
        "comments_untouched": result.comments_untouched,
        "changes": [change.as_dict() for change in result.changes],
        "saved": saved,
    }
    if args.report:
        _write_json(Path(args.report), payload)
    if args.json:
        _write_json(None, payload)
    else:
        print(
            f"remapped {source_path} -> {target}  "
            f"records={len(result.document.records)}  "
            f"changed_records={result.changed_records}  "
            f"changed_operands={len(result.changes)}"
        )
        print(
            f"parser readback: PASS  records={saved['parser']['records']}  "
            f"encoding={saved['parser']['encoding']}"
        )
        if saved.get("native"):
            print(
                "native syntax: PASS  "
                f"error_count={saved['native']['error_count']}"
            )
        if result.unmatched_sources:
            print("unmatched sources: " + ", ".join(result.unmatched_sources))
        _print_save_metadata(saved)
    if args.open:
        open_in_editor(target, editor_path=args.editor)
        print(f"opened in LadEditor: {target}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.lad).resolve()
    inspection = inspect_lad(path, encoding=args.encoding)
    issues = inspection["integrity"]["issues"]
    native_result: dict[str, Any] | None = None
    native_error: str | None = None
    if not args.no_native:
        try:
            native_result = validate_native(
                path,
                dll_path=args.dll,
                checker_path=args.checker,
            )
        except NativeValidationError as exc:
            native_error = str(exc)
    payload = {
        "file": str(path),
        "sha256": inspection["source"]["sha256"],
        "records": inspection["integrity"]["physical_records"],
        "parser_issues": issues,
        "native": native_result,
        "native_error": native_error,
    }
    if args.json:
        _write_json(None, payload)
    else:
        print(f"file    : {path}")
        print(f"sha256  : {inspection['source']['sha256']}")
        print(f"records : {inspection['integrity']['physical_records']}")
        print(f"parser  : {'PASS' if not issues else 'ISSUES'}")
        if native_result is not None:
            print(
                "native  : PASS  "
                f"error_count={native_result['error_count']}"
            )
        elif native_error is not None:
            print(f"native  : FAIL  {native_error}")
        else:
            print("native  : skipped")
    if issues or native_error is not None or (native_result is None and not args.no_native):
        return 4
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    left = LadDocument.load(args.left)
    right = LadDocument.load(args.right)
    left_records = left.records
    right_records = right.records
    differences: list[dict[str, Any]] = []
    limit = max(len(left_records), len(right_records))
    for index in range(limit):
        a = left_records[index] if index < len(left_records) else None
        b = right_records[index] if index < len(right_records) else None
        if a != b:
            differences.append(
                {
                    "record": index,
                    "left": a.hex(" ").upper() if a is not None else None,
                    "right": b.hex(" ").upper() if b is not None else None,
                }
            )
    changed_cells: list[dict[str, Any]] = []
    for difference in differences[:args.context]:
        index = difference["record"]
        if index >= len(left_records) or index >= len(right_records):
            continue
        a = left_records[index]
        b = right_records[index]
        for offset in range(0, 40, 4):
            if a[offset:offset + 4] != b[offset:offset + 4]:
                changed_cells.append(
                    {
                        "record": index,
                        "column": offset // 4,
                        "left": a[offset:offset + 4].hex(" ").upper(),
                        "right": b[offset:offset + 4].hex(" ").upper(),
                    }
                )
    result = {
        "left": str(Path(args.left).resolve()),
        "right": str(Path(args.right).resolve()),
        "same": left.to_bytes() == right.to_bytes(),
        "left_sha256": left.sha256,
        "right_sha256": right.sha256,
        "left_records": len(left_records),
        "right_records": len(right_records),
        "different_records": len(differences),
        "differences": differences,
        "changed_cells": changed_cells,
    }
    if args.json:
        _write_json(None, result)
    else:
        print(f"left  : {result['left']}")
        print(f"right : {result['right']}")
        print(f"same  : {result['same']}")
        print(
            f"records: {result['left_records']} -> {result['right_records']}, "
            f"{result['different_records']} changed/added/removed record positions"
        )
        if differences:
            print("first differences:")
            for difference in differences[:args.context]:
                print(
                    f"  r{difference['record']:04d} left={difference['left']} "
                    f"right={difference['right']}"
                )
    return 0 if result["same"] else 1


def _cmd_open(args: argparse.Namespace) -> int:
    path = Path(args.lad).resolve()
    if not path.is_file():
        raise LadValidationError(f"file does not exist: {path}")
    process = open_in_editor(
        path,
        editor_path=args.editor,
        wait=args.wait,
    )
    if not args.wait:
        print(f"started LadEditor PID {process.pid} for {path}")
    return 0


def _add_native_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dll",
        help="path to LadDLL.dll (default: installed LadEditor or SYNTEC_LAD_DLL)",
    )
    parser.add_argument(
        "--checker",
        help="path to the x86 LadSyntaxCheck.exe helper",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syntec-lad",
        description="Read, generate, patch, and validate Syntec MLCEdit .lad files.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="show programs, comments, records, and network candidates")
    inspect.add_argument("lad")
    inspect.add_argument("--encoding", default="auto", help="auto, big5, gb2312, or gb18030")
    inspect.add_argument("--json", action="store_true", help="emit full JSON")
    inspect.add_argument("--output", help="write output to this file")
    inspect.add_argument("--rows", type=int, default=0, help="also show this many preview rows")
    inspect.set_defaults(func=_cmd_inspect)

    build = sub.add_parser("build", help="compile a reviewed plan from scratch")
    build.add_argument("plan")
    build.add_argument("output")
    build.add_argument("--encoding", help="new-file comment encoding (default: plan or gb2312)")
    build.add_argument("--in-place", action="store_true", help="atomically replace output and create a .bak")
    build.add_argument("--no-native", action="store_true", help="skip the LadDLL syntax check")
    build.add_argument("--open", action="store_true", help="open the result in MLCEdit after validation")
    build.add_argument("--editor", help="path to MLCEdit.exe")
    _add_native_options(build)
    build.set_defaults(func=_cmd_build)

    patch = sub.add_parser("patch", help="apply a hash-pinned syntec-lad-change/v1 document")
    patch.add_argument("lad")
    patch.add_argument("change")
    patch.add_argument("output", nargs="?", help="output path; omit only with --in-place")
    patch.add_argument("--in-place", action="store_true", help="atomically replace the source and create a .bak")
    patch.add_argument("--no-native", action="store_true", help="skip the LadDLL syntax check")
    patch.add_argument("--open", action="store_true", help="open the result in MLCEdit after validation")
    patch.add_argument("--editor", help="path to MLCEdit.exe")
    _add_native_options(patch)
    patch.set_defaults(func=_cmd_patch)

    remap = sub.add_parser(
        "remap",
        help="simultaneously replace PLC-bit operands while preserving record bytes",
    )
    remap.add_argument("lad")
    remap.add_argument("output", nargs="?", help="output path; omit only with --in-place")
    remap.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="SOURCE=TARGET",
        help="PLC bit mapping; repeat for each address (for example I0=I400)",
    )
    remap.add_argument("--mapping-file", help="JSON object of SOURCE: TARGET mappings")
    remap.add_argument("--expected-sha256", help="reject the source when this hash differs")
    remap.add_argument(
        "--require-all-sources",
        action="store_true",
        help="fail when any mapping source is absent from the ladder operands",
    )
    remap.add_argument("--report", help="write the full JSON remap report here")
    remap.add_argument("--json", action="store_true", help="print the full JSON report")
    remap.add_argument("--in-place", action="store_true", help="atomically replace the source and create a .bak")
    remap.add_argument("--overwrite", action="store_true", help="replace an existing output path")
    remap.add_argument("--no-native", action="store_true", help="skip the LadDLL syntax check")
    remap.add_argument("--open", action="store_true", help="open the result in MLCEdit after validation")
    remap.add_argument("--editor", help="path to MLCEdit.exe")
    _add_native_options(remap)
    remap.set_defaults(func=_cmd_remap)

    validate = sub.add_parser("validate", help="parse and optionally run the native LadDLL check")
    validate.add_argument("lad")
    validate.add_argument("--encoding", default="auto")
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--no-native", action="store_true")
    _add_native_options(validate)
    validate.set_defaults(func=_cmd_validate)

    diff = sub.add_parser("diff", help="compare two .lad files at record and cell granularity")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--json", action="store_true")
    diff.add_argument("--context", type=int, default=8, help="number of differing records to show")
    diff.set_defaults(func=_cmd_diff)

    opener = sub.add_parser("open", help="open a .lad in MLCEdit for human review")
    opener.add_argument("lad")
    opener.add_argument("--editor", help="path to MLCEdit.exe")
    opener.add_argument("--wait", action="store_true", help="wait for MLCEdit to exit")
    opener.set_defaults(func=_cmd_open)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except LadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"file error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
