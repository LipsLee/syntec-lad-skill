"""Read-only inspection and network-candidate discovery for .lad files."""

from __future__ import annotations

import hashlib
import struct
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import LadFormatError
from .model import (
    COMMENT_TYPE_NAMES,
    LadDocument,
    iter_comment_blocks,
    read_comment,
)
from .opcodes import (
    CONNECTION_LD,
    CONNECTION_LU,
    CONNECTION_RD,
    CONNECTION_RU,
    GROUP_NAMES,
    OPERAND_TYPE_NAMES,
    STRUCTURAL_OPCODES,
    opcode_meta,
    opcode_name,
)


INSPECTION_SCHEMA = "syntec-lad-inspection/v1"

_NO_OPERAND = {0x00, 0x0D, 0xCF, 0xD0, 0xFD}
_STRING_OPERAND = {0xCA, 0xCB, 0xD4, 0xD5}
_CONNECTION_BITS = (
    (CONNECTION_RU, "RU"),
    (CONNECTION_RD, "RD"),
    (CONNECTION_LU, "LU"),
    (CONNECTION_LD, "LD"),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _u16(cell: bytes, offset: int) -> int:
    return struct.unpack_from("<H", cell, offset)[0]


def _s16(cell: bytes, offset: int) -> int:
    return struct.unpack_from("<h", cell, offset)[0]


def _decode_text(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace").rstrip("\x00")


def _flags(value: int) -> list[str]:
    return [name for bit, name in _CONNECTION_BITS if value & bit]


def _decode_plc_bits(value: int) -> str:
    low12 = value & 0xFFF
    upper = value >> 12
    if upper == 0:
        index = low12 & 0x1FF
        kind = low12 >> 9
        if kind <= 4:
            return f"{'IOCSA'[kind]}{index:03d}"
        return f"BIT?{value:04X}"
    if 1 <= upper <= 5:
        register = (low12 >> 5) + (upper - 1) * 128
        bit = low12 & 0x1F
        return f"R{register:04d}.{bit:02d}"
    return f"BIT?{value:04X}"


def _indirect(cell: bytes, address: int) -> bool:
    if len(cell) < 8:
        return False
    if address == 2:
        return bool(cell[6] & 0x02)
    if address == 4:
        return bool(cell[6] & 0x01)
    return False


def _decode_operand(
    cell: bytes,
    address: int,
    operand_type: int,
    encoding: str,
) -> dict[str, Any] | None:
    if operand_type < 0:
        return None
    if address + 1 >= len(cell):
        return {
            "address": address,
            "type": operand_type,
            "type_name": OPERAND_TYPE_NAMES.get(operand_type, "Unknown"),
            "error": "operand exceeds the physical cell",
        }
    raw_u16 = _u16(cell, address)
    raw_i16 = _s16(cell, address)
    if operand_type == 0:
        value = _decode_plc_bits(raw_u16)
    elif operand_type == 1:
        value = f"TM{raw_i16:03d}"
    elif operand_type == 2:
        value = f"CT{raw_i16:03d}"
    elif operand_type == 3:
        prefix = "@" if _indirect(cell, address) else ""
        value = f"{prefix}R{raw_u16:04d}"
    elif operand_type == 4:
        value = f"#{raw_i16:05d}"
    elif operand_type == 5:
        value = _decode_text(cell[address:address + 6], encoding)
    elif operand_type == 6:
        value = str(raw_i16)
    else:
        value = f"OP?{raw_u16:04X}"
    return {
        "address": address,
        "type": operand_type,
        "type_name": OPERAND_TYPE_NAMES.get(operand_type, "Unknown"),
        "raw_u16": raw_u16,
        "raw_i16": raw_i16,
        "value": value,
    }


def decode_cell(
    record: bytes,
    offset: int,
    *,
    encoding: str,
    record_index: int = 0,
    logical_row: int = 0,
) -> dict[str, Any]:
    if len(record) != 40 or offset < 0 or offset >= 40:
        raise LadFormatError("cell offset is outside a 40-byte record")
    raw_opcode = record[offset + 1]
    meta = opcode_meta(raw_opcode)
    width = 8 if meta.is_bicell else 4
    cell = record[offset:min(offset + width, 40)]
    issues: list[str] = []
    if len(cell) < width:
        issues.append(f"two-cell instruction exceeds the 40-byte row at column {offset // 4}")
        cell = cell.ljust(width, b"\x00")

    operands: list[dict[str, Any]] = []
    if raw_opcode not in _NO_OPERAND and meta.mapped != 0:
        if raw_opcode in _STRING_OPERAND:
            operands.append(
                {
                    "address": 2,
                    "type": 5,
                    "type_name": OPERAND_TYPE_NAMES[5],
                    "raw_u16": None,
                    "raw_i16": None,
                    "value": _decode_text(cell[2:8], encoding),
                }
            )
        else:
            first = _decode_operand(cell, 2, meta.op1_type, encoding)
            if first is not None:
                operands.append(first)
            if width == 8:
                second = _decode_operand(cell, 4, meta.op2_type, encoding)
                if second is not None:
                    operands.append(second)

    values = [str(item.get("value", "")) for item in operands]
    result = {
        "column": offset // 4,
        "byte_offset": offset,
        "size": width,
        "raw_hex": cell.hex(" ").upper(),
        "connection": record[offset],
        "connection_flags": _flags(record[offset]),
        "opcode": raw_opcode,
        "opcode_hex": f"0x{raw_opcode:02X}",
        "mnemonic": opcode_name(raw_opcode),
        "group": meta.group,
        "group_name": meta.group_name,
        "function": meta.function,
        "is_bicell": meta.is_bicell,
        "known": meta.mapped != 0 or raw_opcode in (0x00, 0x0D),
        "operands": operands,
        "value": " ".join(value for value in values if value),
        "indirect_byte": cell[6] if width == 8 else None,
        "extra_byte": cell[7] if width == 8 else None,
        "issues": issues,
    }
    if not result["known"]:
        result["issues"].append(f"unknown opcode 0x{raw_opcode:02X}")
    return result


def _decode_code_row(record: bytes, logical_row: int, encoding: str) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    offset = 0
    while offset < 40:
        cell = decode_cell(
            record,
            offset,
            encoding=encoding,
            record_index=logical_row,
            logical_row=logical_row,
        )
        cells.append(cell)
        offset += int(cell["size"])
    return {
        "kind": "code",
        "opcode": record[1],
        "is_structural": record[1] in STRUCTURAL_OPCODES,
        "raw_hex": record.hex(" ").upper(),
        "cells": cells,
    }


def _row_vertical_links(record: bytes) -> dict[int, int]:
    links: dict[int, int] = {}
    offset = 0
    while offset < 40:
        raw = record[offset + 1]
        width = 8 if opcode_meta(raw).is_bicell else 4
        flags = record[offset]
        if flags & 0x0F:
            links[offset // 4] = flags & 0x0F
        offset += width
    return links


def rows_connected(upper: bytes, lower: bytes) -> bool:
    """Return whether two adjacent code records are joined by a vertical bus."""

    upper_links = _row_vertical_links(upper)
    lower_links = _row_vertical_links(lower)
    for column, flags in upper_links.items():
        if flags & CONNECTION_RD and lower_links.get(column, 0) & CONNECTION_RU:
            return True
        if flags & CONNECTION_LD and lower_links.get(column, 0) & CONNECTION_LU:
            return True
    # A one-sided flag is an invalid ladder connection, not evidence of a
    # multi-row rung.  Treating it as a connection would merge unrelated
    # networks and produce unsafe patch boundaries.  LadDLL reports the
    # malformed connection separately when the file is validated.
    return False


def _one_line(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _cell_text(cell: dict[str, Any]) -> str:
    if cell["opcode"] in (0x00, 0x0D):
        return ""
    value = cell.get("value", "")
    return f"{cell['mnemonic']} {value}".strip()


def _preview(cells: list[dict[str, Any]]) -> str:
    parts = [_cell_text(cell) for cell in cells]
    return " | ".join(part for part in parts if part)


def _comment_association(
    comments: dict[int, dict[str, Any]],
    start_record: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for comment in comments.values():
        if comment["type"] == 1 and comment["record_end"] == start_record - 1:
            result.append(comment)
    return result


def _find_program_ranges(
    records: list[bytes],
    comments_by_end: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    end_record = next(
        (index for index, record in enumerate(records) if record[1] == 0xCF),
        None,
    )
    if end_record is None:
        issues.append("main END instruction was not found")
        end_record = len(records) - 1

    programs: list[dict[str, Any]] = [
        {
            "name": "MAIN",
            "kind": "main",
            "record_start": 0,
            "record_end": end_record,
            "end_record": end_record,
            "comment_start": None,
        }
    ]
    cursor = end_record + 1
    while cursor < len(records):
        label_record = next(
            (
                index
                for index in range(cursor, len(records))
                if records[index][1] == 0xD5
            ),
            None,
        )
        if label_record is None:
            break
        rts_record = next(
            (
                index
                for index in range(label_record + 1, len(records))
                if records[index][1] == 0xD0
            ),
            None,
        )
        if rts_record is None:
            issues.append(f"LAB at record {label_record} has no matching RTS")
            break
        label_name = _decode_text(records[label_record][2:8], "ascii")
        comment_start = None
        comment = comments_by_end.get(label_record - 1)
        if comment is not None and comment["type"] == 3:
            comment_start = comment["record_start"]
        programs.append(
            {
                "name": label_name,
                "kind": "subprogram",
                "record_start": comment_start if comment_start is not None else label_record,
                "record_end": rts_record,
                "label_record": label_record,
                "end_record": rts_record,
                "comment_start": comment_start,
            }
        )
        cursor = rts_record + 1
    return programs, issues


def inspect_document(document: LadDocument) -> dict[str, Any]:
    records = document.records
    issues: list[str] = []
    comments: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    record_to_row: dict[int, int] = {}
    physical_to_comment_start: dict[int, int] = {}

    index = 0
    while index < len(records):
        record = records[index]
        if record[1] == 0 and record[3] != 0:
            block = read_comment(records, index, document.encoding)
            comment = {
                "record_start": block.start_record,
                "record_end": block.end_record,
                "record_count": block.record_count,
                "type": block.comment_type,
                "type_name": COMMENT_TYPE_NAMES.get(block.comment_type, "unknown"),
                "length": block.length,
                "text": block.text,
            }
            comments[block.start_record] = comment
            for physical in range(block.start_record, block.end_record + 1):
                physical_to_comment_start[physical] = block.start_record
            row_index = len(rows)
            for physical in range(block.start_record, block.end_record + 1):
                record_to_row[physical] = row_index
            rows.append(
                {
                    "kind": "comment",
                    "record_start": block.start_record,
                    "record_end": block.end_record,
                    "record_count": block.record_count,
                    "comment": comment,
                    "cells": [],
                }
            )
            index = block.end_record + 1
            continue

        row = _decode_code_row(record, len(rows), document.encoding)
        row.update({"record_start": index, "record_end": index, "record_count": 1})
        record_to_row[index] = len(rows)
        rows.append(row)
        for cell in row["cells"]:
            if cell["issues"]:
                issues.extend(
                    f"record {index} column {cell['column']}: {text}"
                    for text in cell["issues"]
                )
        index += 1

    comments_by_end = {
        comment["record_end"]: comment for comment in comments.values()
    }
    programs, program_issues = _find_program_ranges(records, comments_by_end)
    issues.extend(program_issues)

    structural_records = {
        i: records[i][1]
        for i in range(len(records))
        if records[i][1] in STRUCTURAL_OPCODES
    }
    comment_starts = set(comments)
    code_records = [
        i
        for i in range(len(records))
        if i not in physical_to_comment_start and i not in structural_records
    ]

    candidates: list[dict[str, Any]] = []
    current: list[int] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        start = current[0]
        end = current[-1]
        preview_cells: list[dict[str, Any]] = []
        for record_index in current:
            logical = rows[record_to_row[record_index]]
            preview_cells.extend(logical["cells"])
        candidate = {
            "id": f"N{len(candidates) + 1:04d}",
            "record_start": start,
            "record_end": end,
            "record_count": end - start + 1,
            "sha256": _sha256(b"".join(records[start:end + 1])),
            "preview": _preview(preview_cells),
            "leading_comments": _comment_association(comments, start),
            "program": next(
                (
                    program["name"]
                    for program in programs
                    if program["record_start"] <= start <= program["record_end"]
                ),
                None,
            ),
        }
        candidates.append(candidate)
        current = []

    previous_record: int | None = None
    for record_index in code_records:
        if record_index in structural_records:
            flush()
            previous_record = None
            continue
        if previous_record is not None and record_index == previous_record + 1:
            if not rows_connected(records[previous_record], records[record_index]):
                flush()
        else:
            flush()
        current.append(record_index)
        previous_record = record_index
    flush()

    candidates_by_record: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        for record_index in range(candidate["record_start"], candidate["record_end"] + 1):
            candidates_by_record[record_index] = candidate

    for program in programs:
        start = int(program["record_start"])
        end = int(program["record_end"])
        program_candidates = [
            candidate
            for candidate in candidates
            if start <= candidate["record_start"] and candidate["record_end"] <= end
        ]
        program["network_candidates"] = program_candidates
        program["network_count"] = len(program_candidates)

    comments_list = [comments[key] for key in sorted(comments)]
    opcode_counts: Counter[int] = Counter()
    connection_counts: Counter[str] = Counter()
    instruction_count = 0
    bicell_count = 0
    labels: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] != "code":
            continue
        for cell in row["cells"]:
            opcode_counts[cell["opcode"]] += 1
            if cell["is_bicell"]:
                bicell_count += 1
            if cell["opcode"] not in (0x00, 0x0D):
                instruction_count += 1
            for flag in cell["connection_flags"]:
                connection_counts[flag] += 1
            if cell["opcode"] == 0xD5:
                labels.append(
                    {
                        "record": row["record_start"],
                        "column": cell["column"],
                        "name": cell["value"],
                    }
                )

    comment_type_counts = Counter(comment["type"] for comment in comments_list)
    source_path = str(document.source_path) if document.source_path else None
    result: dict[str, Any] = {
        "schema": INSPECTION_SCHEMA,
        "source": {
            "path": source_path,
            "size": len(document.to_bytes()),
            "sha256": document.sha256,
            "encoding": document.encoding,
            "trailer": document.trailer.hex(" ").upper(),
            "truncated": False,
        },
        "version": document.version,
        "integrity": {
            "trailer_ok": True,
            "physical_records": len(records),
            "logical_rows": len(rows),
            "code_records": sum(1 for row in rows if row["kind"] == "code"),
            "comment_blocks": len(comments_list),
            "issues": issues,
        },
        "statistics": {
            "comments": len(comments_list),
            "comment_types": {
                str(kind): {
                    "name": COMMENT_TYPE_NAMES.get(kind, "unknown"),
                    "count": count,
                }
                for kind, count in sorted(comment_type_counts.items())
            },
            "cells": sum(len(row["cells"]) for row in rows if row["kind"] == "code"),
            "instruction_cells": instruction_count,
            "bicells": bicell_count,
            "connections": dict(sorted(connection_counts.items())),
            "opcodes": {
                f"0x{opcode:02X}": {
                    "count": count,
                    "mnemonic": opcode_name(opcode),
                }
                for opcode, count in sorted(opcode_counts.items())
            },
            "labels": len(labels),
        },
        "programs": programs,
        "comments": comments_list,
        "rows": rows,
        "network_candidates": candidates,
        "labels": labels,
    }
    return result


def inspect_lad(
    path: Path | str,
    *,
    encoding: str | None = "auto",
) -> dict[str, Any]:
    document = LadDocument.load(path, encoding=encoding)
    result = inspect_document(document)
    result["source"]["path"] = str(Path(path).resolve())
    return result


def render_inspection(result: dict[str, Any], *, preview_rows: int = 0) -> str:
    source = result["source"]
    integrity = result["integrity"]
    stats = result["statistics"]
    lines = [
        "SYNTEC LadEditor inspection",
        f"Source        : {source['path'] or '<memory>'}",
        f"Size / SHA256 : {source['size']} bytes / {source['sha256']}",
        f"Encoding      : {source['encoding']}",
        f"Records       : {integrity['physical_records']} physical, "
        f"{integrity['logical_rows']} logical rows",
        f"Version       : {result['version'] if result['version'] is not None else 'unknown'}",
        f"Comments      : {stats['comments']}",
        f"Instructions  : {stats['instruction_cells']} cells, {stats['bicells']} two-cell",
        f"Issues        : {len(integrity['issues'])}",
        "",
        "Programs:",
    ]
    for program in result["programs"]:
        lines.append(
            f"  {program['name']:<8} {program['kind']:<10} "
            f"records {program['record_start']}..{program['record_end']} "
            f"({program.get('network_count', 0)} candidate networks)"
        )
    lines.append("")
    lines.append("Network candidates:")
    for candidate in result["network_candidates"]:
        comment = ""
        if candidate.get("leading_comments"):
            comment = "  // " + _one_line(candidate["leading_comments"][0]["text"])
        lines.append(
            f"  {candidate['id']} r{candidate['record_start']}..{candidate['record_end']} "
            f"sha256={candidate['sha256'][:16]}  {candidate['preview']}{comment}"
        )
    if preview_rows:
        lines.extend(["", "Rows:"])
        for row in result["rows"][:preview_rows]:
            if row["kind"] == "comment":
                lines.append(
                    f"  {row['record_start']:04d} COMMENT "
                    f"{_one_line(row['comment']['text'])}"
                )
                continue
            lines.append(
                f"  {row['record_start']:04d} {_preview(row['cells'])}"
            )
    if result["integrity"]["issues"]:
        lines.extend(["", "Issues:"])
        lines.extend(f"  - {issue}" for issue in result["integrity"]["issues"])
    return "\n".join(lines) + "\n"
