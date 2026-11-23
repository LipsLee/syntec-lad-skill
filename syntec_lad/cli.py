"""Command-line interface for the Syntec ladder SDK."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .address_db import lookup_address
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


def _apply_inspection_filter(
    result: dict[str, Any],
    program: str | None,
    network: str | None,
) -> tuple[dict[str, Any], str | None]:
    from .inspection import apply_inspection_filter

    return apply_inspection_filter(result, program=program, network=network)


def _cmd_inspect(args: argparse.Namespace) -> int:
    result = inspect_lad(args.lad, encoding=args.encoding)
    result, note = _apply_inspection_filter(
        result,
        getattr(args, "program", None),
        getattr(args, "network", None),
    )
    output = Path(args.output) if args.output else None
    if output is not None and output.resolve() == Path(args.lad).resolve():
        raise LadValidationError("inspect --output must not overwrite the input ladder")
    if args.json:
        _write_json(output, result)
    else:
        text = render_inspection(result, preview_rows=args.rows)
        if note is not None:
            text = note + "\n\n" + text
        if output is None:
            sys.stdout.write(text)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
    return 0


def _cmd_lookup(args: argparse.Namespace) -> int:
    from .knowledge import search_knowledge

    hits = lookup_address(args.query, limit=args.limit)
    knowledge = {"query": args.query, "hits": [], "hints": []}
    if not hits and not args.addresses_only:
        knowledge = search_knowledge(args.query, limit=args.limit)
    payload = {
        "query": args.query,
        "count": len(hits),
        "hits": hits,
        "knowledge": knowledge,
    }
    if args.json:
        _write_json(None, payload)
    elif hits:
        for hit in hits:
            descriptions = " | ".join(hit["descriptions"])
            print(f"{hit['address']}  [{hit['type']}]  {descriptions}")
    elif knowledge["hits"]:
        print(
            f"no address match for {args.query!r}; "
            "knowledge-base sections mentioning it:"
        )
        for item in knowledge["hits"]:
            print(f"  {item['file']}:{item['line']}  {item['heading']}")
            if item["excerpt"]:
                print(f"      {item['excerpt'][:110]}")
        for hint in knowledge["hints"]:
            print(f"  hint: {hint}")
    else:
        print(
            f"no match for {args.query!r} in the bundled 602-address index or the "
            "knowledge base; see references/panming/address-map.md for the full "
            "address model"
        )
    return 0 if (hits or knowledge["hits"]) else 1


def _cmd_lint(args: argparse.Namespace) -> int:
    from .lint import lint_document, render_lint

    document = LadDocument.load(args.lad)
    result = lint_document(document, io_max_uses=args.io_max_uses)
    output = Path(args.output) if args.output else None
    if args.json:
        _write_json(output, result)
    else:
        text = render_lint(result, limit=None if args.limit <= 0 else args.limit)
        if output is None:
            sys.stdout.write(text)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
    return 0 if result["ok"] else 1


def _cmd_plan_lint(args: argparse.Namespace) -> int:
    from .plan_lint import lint_plan, render_plan_lint

    path = Path(args.plan)
    result = lint_plan(_json_load(path), source_path=path.resolve())
    output = Path(args.output) if args.output else None
    if args.json:
        _write_json(output, result)
    else:
        text = render_plan_lint(
            result,
            limit=None if args.limit <= 0 else args.limit,
        )
        if output is None:
            sys.stdout.write(text)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
    return 0 if result["ok"] else 1


def _cmd_crosscheck(args: argparse.Namespace) -> int:
    from .crosscheck import crosscheck, render_crosscheck
    from .delivery import resolve_inputs

    inputs = resolve_inputs(
        lad=args.lad,
        delivery=args.delivery,
        pxt=args.pxt,
        param_xml=args.param_xml,
        alarm_xml=args.alarm_xml,
        io=args.io,
    )
    result = crosscheck(
        Path(inputs["lad"]),
        pxt_path=Path(inputs["pxt"]) if inputs["pxt"] else None,
        param_xml_paths=[Path(item) for item in inputs["param_xml"]],
        alarm_xml_path=Path(inputs["alarm_xml"]) if inputs["alarm_xml"] else None,
        io_path=Path(inputs["io"]) if inputs["io"] else None,
    )
    if inputs["delivery"]:
        result["delivery"] = {
            "root": inputs["delivery"]["root"],
            "layout": inputs["delivery"]["layout"],
        }
    output = Path(args.output) if args.output else None
    if args.json:
        _write_json(output, result)
    else:
        text = render_crosscheck(result, context=args.context)
        if output is None:
            sys.stdout.write(text)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
    return 0 if result["ok"] else 1


def _cmd_discover(args: argparse.Namespace) -> int:
    from .delivery import discover, render_discovery

    result = discover(args.path)
    if args.json:
        _write_json(None, result)
    else:
        sys.stdout.write(render_discovery(result))
    return 0 if result["files"].get("lad") else 1


def _cmd_zhconv(args: argparse.Namespace) -> int:
    from .zhconv import find_simplified, to_simplified, to_traditional

    text = args.text
    if text == "-":
        text = sys.stdin.read()
    converted = to_simplified(text) if args.to_simplified else to_traditional(text)
    payload = {
        "direction": "simplified" if args.to_simplified else "traditional",
        "input": text,
        "output": converted,
        "changed": converted != text,
        "simplified_found": find_simplified(text),
    }
    if args.json:
        _write_json(None, payload)
    else:
        print(converted)
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
    from .lad_diff import diff_documents, render_diff

    left = LadDocument.load(args.left)
    right = LadDocument.load(args.right)
    result = diff_documents(left, right, context=args.context)
    if args.json:
        _write_json(None, result)
    else:
        sys.stdout.write(render_diff(result, context=args.context))
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
    parser.add_argument("--version", action="version", version="%(prog)s 0.2.3")
    sub = parser.add_subparsers(dest="command", required=True)

    lookup = sub.add_parser(
        "lookup",
        help="look up a C/S/R address or keyword in the bundled engineering index",
    )
    lookup.add_argument("query", help="address (C6/C006, R600.1) or keyword")
    lookup.add_argument("--json", action="store_true", help="emit JSON")
    lookup.add_argument("--limit", type=int, default=20, help="maximum keyword hits")
    lookup.add_argument(
        "--addresses-only",
        action="store_true",
        help="do not fall back to the knowledge base when no address matches",
    )
    lookup.set_defaults(func=_cmd_lookup)

    lint = sub.add_parser(
        "lint",
        help="static rule checks: duplicate coils, S as coil, reused I/O, ranges, comments",
    )
    lint.add_argument("lad")
    lint.add_argument("--json", action="store_true", help="emit JSON")
    lint.add_argument("--output", help="write the report to this file")
    lint.add_argument("--limit", type=int, default=40, help="max issues to print (0 = all)")
    lint.add_argument(
        "--io-max-uses",
        type=int,
        default=5,
        help="warn when one I/O point is used in this many networks (default 5)",
    )
    lint.set_defaults(func=_cmd_lint)

    plan_lint = sub.add_parser(
        "plan-lint",
        help="audit a plan for equivalent adjacent conditions and mixed action widths",
    )
    plan_lint.add_argument("plan")
    plan_lint.add_argument("--json", action="store_true", help="emit JSON")
    plan_lint.add_argument("--output", help="write the report to this file")
    plan_lint.add_argument(
        "--limit",
        type=int,
        default=40,
        help="max issues to print (0 = all)",
    )
    plan_lint.set_defaults(func=_cmd_plan_lint)

    cross = sub.add_parser(
        "crosscheck",
        help="check .lad against paramext.pxt, ParamExt_*.xml, AlarmPLC_*.xml and an IO table",
    )
    cross.add_argument("lad", nargs="?", help=".lad file; omit when --delivery is given")
    cross.add_argument(
        "--delivery",
        help="MB/SB/package backup directory; ladder, pxt, XML and IO map are discovered automatically",
    )
    cross.add_argument("--pxt", help="paramext.pxt defining which extended parameters exist")
    cross.add_argument(
        "--param-xml",
        action="append",
        default=[],
        metavar="XML",
        help="ParamExt_CHS/CHT/COM.xml; repeat to compare languages",
    )
    cross.add_argument("--alarm-xml", help="AlarmPLC_*.xml with the alarm bit text")
    cross.add_argument("--io", help="IO table (.dat/.csv/.txt, address<TAB>description)")
    cross.add_argument("--json", action="store_true", help="emit JSON")
    cross.add_argument("--output", help="write the report to this file")
    cross.add_argument("--context", type=int, default=12, help="max items per section")
    cross.set_defaults(func=_cmd_crosscheck)

    discover = sub.add_parser(
        "discover",
        help="report which ladder/parameter/alarm files a backup directory contains",
    )
    discover.add_argument("path", help="MB/SB/package backup directory or a single .lad file")
    discover.add_argument("--json", action="store_true", help="emit JSON")
    discover.set_defaults(func=_cmd_discover)

    zhconv = sub.add_parser(
        "zhconv",
        help="convert comment text between simplified and traditional Chinese",
    )
    zhconv.add_argument("text", help="text to convert, or - to read stdin")
    zhconv.add_argument(
        "--to-simplified",
        action="store_true",
        help="convert traditional to simplified (default: simplified to traditional)",
    )
    zhconv.add_argument("--json", action="store_true", help="emit JSON with per-character hits")
    zhconv.set_defaults(func=_cmd_zhconv)

    inspect = sub.add_parser("inspect", help="show programs, comments, records, and network candidates")
    inspect.add_argument("lad")
    inspect.add_argument("--encoding", default="auto", help="auto, big5, gb2312, or gb18030")
    inspect.add_argument("--json", action="store_true", help="emit full JSON")
    inspect.add_argument("--output", help="write output to this file")
    inspect.add_argument("--rows", type=int, default=0, help="also show this many preview rows")
    inspect.add_argument(
        "--program",
        help="only report networks inside this program (e.g. MAIN, 7)",
    )
    inspect.add_argument(
        "--network",
        help="only report this network candidate (e.g. N0016 or 16)",
    )
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

    diff = sub.add_parser(
        "diff",
        help="compare two .lad files aligned by comment/network segments",
    )
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
