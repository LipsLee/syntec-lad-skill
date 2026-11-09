#!/usr/bin/env python3
"""Parser and disassembler for Syntec MLCEdit .lad ladder files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_VERSION = "1.0.0"

PLCOP_MAP = (
    0, 1, 0, 17, 0, 34, 0, 50, 0, 1057, 1073, 2113, 2129, 7, 2, 0,
    18, 0, 66, 0, 82, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 28163, 0, 0, 0, 0, 0, 29187, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 28179, 0, 0, 0, 0, 0, 29203, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 28195, 0, 0, 0, 0, 0, 29219, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 28211, 0, 0, 0, 0, 0, 29235, 28227, 0,
    0, 0, 28243, 0, 0, 0, 28259, 0, 0, 0, 29251, 0, 29267, 0, 29283, 0,
    29299, 0, 28275, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 28291, 0, 0, 0, 0, 0, 29315, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 28307, 0, 0, 0, 0, 0, 29331, 0, 0, 0, 0, 0, 0,
    31411, 61171, 28371, 28387, 52931, 28323, 0, 0, 0, 0, 0, 29347, 0, 0, 0, 0,
    28170, 0, 0, 0, 0, 0, 0, 0, 39432, 31240, 0, 39464, 31288, 31243, 31245, 31261,
    0, 0, 34356, 34308, 34324, 34340, 35333, 35349, 0, 35381, 5638, 5654, 0, 0, 0, 5718,
    5702, 0, 0, 0, 5670, 5686, 0, 31246, 31244, 28188, 28204, 29244, 28236, 28252, 28268, 28284,
    35397, 35413, 26116, 26132, 26148, 27141, 27157, 26164, 0, 27205, 27221, 28300, 28316, 0, 0, 0,
    29214, 39470, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5734, 0, 0,
)

GROUP_NAMES = (
    "Null", "Contact", "Coil", "Function", "Timer", "Counter", "Flow",
    "HorizontalLink", "Axis", "Coord", "Tool", "Keyscan", "RS232",
    "FileSys", "External",
)

GROUP_CHARS = {
    0: ".", 1: "C", 2: "O", 3: "F", 4: "T", 5: "N", 6: "J",
    7: "=", 8: "A", 9: "G", 10: "L", 11: "K", 12: "S", 13: "Z",
    14: "X",
}

OPERAND_TYPE_NAMES = {
    -1: "Null",
    0: "PLCBits",
    1: "Timer",
    2: "Counter",
    3: "Register",
    4: "ConstValue",
    5: "String",
    6: "DeviceNameLead",
}

COMMENT_TYPE_NAMES = {
    1: "line",
    2: "version",
    3: "program",
    4: "variable",
}

CONNECTION_BITS = (
    (0x01, "RU"),
    (0x02, "RD"),
    (0x04, "LU"),
    (0x08, "LD"),
)

DEVICE_NAME_LEAD = (
    "none", "none", "none", "none", "none", "none", "none", "none",
    "AX", "AG", "none", "KS", "RS", "FS", "Fn", "none",
)

RS232_OP3_LEAD = (
    "none", "none", "none", "none", "none", "none", "RS", "RS",
    "SL=", "SL=", "none", "none", "none", "none", "none", "none",
)

RS232_OP4_LEAD = (
    "none", "none", "none", "none", "none", "none", "N=", "none",
    "none", "C=", "none", "none", "none", "none", "none", "none",
)

EXTERNAL_OP3_LEAD = (
    "none", "BG", "none", "none", "none", "none", "none", "none",
    "none", "none", "none", "none", "none", "none", "none", "none",
)

OPCODE_INFO: dict[int, tuple[str, str]] = {
    0: ("NOP", "空单元"),
    1: ("LD", "常开触点"),
    3: ("LDI", "常闭触点"),
    5: ("PLS", "上升沿线圈"),
    7: ("PLF", "下降沿线圈"),
    9: ("LDT", "定时器常开触点"),
    10: ("LDTI", "定时器常闭触点"),
    11: ("LDC", "计数器常开触点"),
    12: ("LDCI", "计数器常闭触点"),
    13: ("LINE", "水平连线"),
    14: ("OUT", "普通输出线圈"),
    16: ("OUTI", "常闭输出线圈"),
    18: ("SET", "置位线圈"),
    20: ("RST", "复位线圈"),
    33: ("ADD", "寄存器到寄存器加法"),
    39: ("ADD", "常量到寄存器加法"),
    51: ("SUB", "寄存器到寄存器减法"),
    57: ("SUB", "常量到寄存器减法"),
    69: ("MUL", "寄存器到寄存器乘法"),
    75: ("MUL", "常量到寄存器乘法"),
    87: ("DIV", "寄存器到寄存器除法"),
    93: ("DIV", "常量到寄存器除法"),
    94: ("CMPG", "寄存器到寄存器大于比较"),
    106: ("CMPG", "常量到寄存器大于比较"),
    98: ("CMPL", "寄存器到寄存器小于比较"),
    108: ("CMPL", "常量到寄存器小于比较"),
    102: ("CMPE", "寄存器到寄存器等于比较"),
    110: ("CMPE", "常量到寄存器等于比较"),
    112: ("MOV", "常量到寄存器传送"),
    114: ("MOV", "寄存器到寄存器传送"),
    129: ("AND", "寄存器到寄存器按位与"),
    135: ("AND", "常量到寄存器按位与"),
    147: ("OR", "寄存器到寄存器按位或"),
    153: ("OR", "常量到寄存器按位或"),
    165: ("XOR", "寄存器到寄存器按位异或"),
    171: ("XOR", "常量到寄存器按位异或"),
    176: ("ROT", "循环移位"),
    184: ("AXRUN", "轴运行，常量参数"),
    185: ("AXRUN", "轴运行，寄存器参数"),
    187: ("AXSTOP", "轴停止"),
    188: ("AXMOVE", "轴移动"),
    189: ("KEYSCAN", "按键扫描"),
    190: ("TCHFILE", "文件系统操作"),
    194: ("TIM", "1 ms 定时器，常量设定"),
    195: ("TIM", "10 ms 定时器，常量设定"),
    196: ("TIM", "100 ms 定时器，常量设定"),
    197: ("TIM", "1000 ms 定时器，常量设定"),
    198: ("CNTUP", "加计数，常量设定"),
    199: ("CNTDOWN", "减计数，常量设定"),
    201: ("CNTRESET", "计数器复位"),
    202: ("JMP", "跳转"),
    203: ("JSR", "调用子程序"),
    207: ("END", "主程序结束"),
    208: ("RTS", "子程序返回"),
    212: ("MSG", "消息/报警"),
    213: ("LAB", "标签"),
    215: ("EXT", "外部功能"),
    216: ("RS232", "串口功能"),
    217: ("ASC>BIN", "ASCII 转二进制"),
    218: ("BIN>ASC", "二进制转 ASCII"),
    219: ("PUTCHAR", "发送字符"),
    220: ("GETCHAR", "接收字符"),
    221: ("HEX>DEC", "十六进制转十进制"),
    222: ("MODR", "串口读取"),
    223: ("MODW", "串口写入"),
    224: ("CNTRINGUP", "环形加计数，常量设定"),
    225: ("CNTRINGDOWN", "环形减计数，常量设定"),
    226: ("TIM", "10 ms 定时器，寄存器设定"),
    227: ("TIM", "100 ms 定时器，寄存器设定"),
    228: ("TIM", "1000 ms 定时器，寄存器设定"),
    229: ("CNTUP", "加计数，寄存器设定"),
    230: ("CNTDOWN", "减计数，寄存器设定"),
    231: ("TIM", "1 ms 定时器，寄存器设定"),
    233: ("CNTRINGUP", "环形加计数，寄存器设定"),
    234: ("CNTRINGDOWN", "环形减计数，寄存器设定"),
    235: ("MODRS", "串口读写"),
    236: ("SRI", "串口接收中断"),
    240: ("BGNDRUN", "后台运行"),
    241: ("BGNDSTOP", "后台停止"),
    253: ("FEND", "快速结束"),
}

TIMER_INTERVAL_MS = {
    194: 1, 195: 10, 196: 100, 197: 1000,
    226: 10, 227: 100, 228: 1000, 231: 1,
}

NO_OPERAND_OPCODES = {0, 13, 207, 208, 253}
STRING_OPERAND_OPCODES = {202, 203, 212, 213}


class LadFormatError(ValueError):
    """Raised when the input cannot be interpreted as a .lad file."""


@dataclass
class CommentBlock:
    record_index: int
    record_count: int
    comment_type: int
    length: int
    text: str
    raw_hex: str
    version: int | None = None
    fields: list[str] | None = None


@dataclass
class Cell:
    column: int
    byte_offset: int
    size: int
    raw_hex: str
    connection: int
    connection_flags: list[str]
    opcode: int
    mnemonic: str
    description: str
    group: int
    group_name: str
    function: int
    is_bicell: bool
    known: bool
    operands: list[dict[str, Any]]
    value: str
    indirect_byte: int | None
    extra_byte: int | None
    timer_interval_ms: int | None


@dataclass
class Row:
    index: int
    record_index: int
    kind: str
    raw_hex: str
    comment: CommentBlock | None = None
    cells: list[Cell] | None = None


def _decode(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace")


def _one_line(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _u16(cell: bytes, address: int) -> int:
    return cell[address] | (cell[address + 1] << 8)


def _s16(cell: bytes, address: int) -> int:
    return struct.unpack_from("<h", cell, address)[0]


def _plcop_meta(opcode: int) -> dict[str, Any]:
    mapped = PLCOP_MAP[opcode]
    return {
        "mapped_value": mapped,
        "group": mapped & 0xF,
        "group_name": GROUP_NAMES[mapped & 0xF],
        "function": (mapped & 0x1F0) >> 4,
        "is_bicell": bool(mapped & 0x200),
        "op1_type": (mapped & 0x1C00) >> 10,
        "op2_type": (mapped & 0xE000) >> 13,
    }


def _connection_flags(value: int) -> list[str]:
    return [name for bit, name in CONNECTION_BITS if value & bit]


def _is_direct_register(cell: bytes, address: int, group: int) -> bool:
    if len(cell) < 8 or group in (4, 12):
        return False
    if address == 2:
        return cell[6] in (2, 3)
    if address == 4:
        return cell[6] in (1, 3)
    return False


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


def _decode_operand(
    cell: bytes,
    address: int,
    operand_type: int,
    group: int,
    encoding: str,
    issues: list[dict[str, Any]],
    record_index: int,
    column: int,
) -> dict[str, Any] | None:
    if operand_type not in OPERAND_TYPE_NAMES or operand_type == -1:
        return None
    if address + 1 >= len(cell):
        issues.append({
            "record": record_index,
            "column": column,
            "message": f"operand at offset {address} exceeds cell",
        })
        return None

    raw_u16 = _u16(cell, address)
    raw_s16 = _s16(cell, address)
    value: str

    if operand_type == 0:
        value = _decode_plc_bits(raw_u16)
    elif operand_type == 1:
        value = f"TM{raw_s16:03d}"
    elif operand_type == 2:
        value = f"CT{raw_s16:03d}"
    elif operand_type == 3:
        prefix = "@R" if _is_direct_register(cell, address, group) else "R"
        value = f"{prefix}{raw_u16:04d}"
    elif operand_type == 4:
        value = f"#{raw_s16:05d}"
    elif operand_type == 5:
        end = min(address + 6, len(cell))
        raw = cell[address:end]
        if len(raw) < 6:
            issues.append({
                "record": record_index,
                "column": column,
                "message": f"six-byte string at offset {address} is truncated",
            })
        value = _decode(raw, encoding).rstrip("\x00")
    else:
        if group == 14:
            value = f"BG{raw_s16:02d}"
        elif 0 <= group < len(DEVICE_NAME_LEAD):
            value = f"{DEVICE_NAME_LEAD[group]}{raw_s16:02d}"
        else:
            value = f"DEV{raw_s16:02d}"

    return {
        "address": address,
        "type": operand_type,
        "type_name": OPERAND_TYPE_NAMES[operand_type],
        "raw_u16": raw_u16,
        "raw_i16": raw_s16,
        "value": value,
    }


def _decode_flow_string(cell: bytes, encoding: str) -> str:
    return _decode(cell[2:8], encoding).rstrip("\x00")


def _extra_operands(cell: bytes, opcode: int, group: int, function: int) -> list[str]:
    if len(cell) < 8:
        return []
    extras: list[str] = []
    byte7 = cell[7]
    byte6 = cell[6]
    if group == 12:
        if function < len(RS232_OP3_LEAD) and RS232_OP3_LEAD[function] != "none" and byte7:
            extras.append(f"{RS232_OP3_LEAD[function]}{byte7:03d}")
        if function < len(RS232_OP4_LEAD) and RS232_OP4_LEAD[function] != "none" and byte6 >> 4:
            extras.append(f"{RS232_OP4_LEAD[function]}{byte6 >> 4:0d}")
    elif group == 14:
        if function == 1 and byte7:
            extras.append(f"BG{byte7:03d}")
    return extras


def _parse_cell(
    row_data: bytes,
    offset: int,
    record_index: int,
    row_index: int,
    encoding: str,
    issues: list[dict[str, Any]],
) -> Cell:
    opcode = row_data[offset + 1]
    meta = _plcop_meta(opcode)
    size = 8 if meta["is_bicell"] else 4
    column = offset // 4
    cell = row_data[offset:min(offset + size, 40)]
    if len(cell) < size:
        issues.append({
            "record": record_index,
            "row": row_index,
            "column": column,
            "message": f"two-cell instruction exceeds 40-byte row (have {len(cell)} bytes)",
        })
        cell = cell.ljust(size, b"\x00")

    mnemonic, description = OPCODE_INFO.get(
        opcode, (f"OP{opcode:02X}", "未识别指令")
    )
    known = opcode in OPCODE_INFO or meta["mapped_value"] != 0
    if not known:
        issues.append({
            "record": record_index,
            "row": row_index,
            "column": column,
            "message": f"unknown opcode 0x{opcode:02X}",
        })

    connection = row_data[offset]
    operands: list[dict[str, Any]] = []

    if opcode in STRING_OPERAND_OPCODES:
        operands.append({
            "address": 2,
            "type": 5,
            "type_name": "String",
            "raw_u16": None,
            "raw_i16": None,
            "value": _decode_flow_string(cell, encoding),
        })
    elif opcode not in NO_OPERAND_OPCODES:
        op1 = _decode_operand(
            cell, 2, meta["op1_type"], meta["group"], encoding,
            issues, record_index, column,
        )
        if op1 is not None:
            operands.append(op1)
        if size == 8:
            op2 = _decode_operand(
                cell, 4, meta["op2_type"], meta["group"], encoding,
                issues, record_index, column,
            )
            if op2 is not None:
                operands.append(op2)

    values = [operand["value"] for operand in operands]
    extras = _extra_operands(cell, opcode, meta["group"], meta["function"])
    values.extend(extras)

    return Cell(
        column=column,
        byte_offset=offset,
        size=size,
        raw_hex=cell.hex(" ").upper(),
        connection=connection,
        connection_flags=_connection_flags(connection),
        opcode=opcode,
        mnemonic=mnemonic,
        description=description,
        group=meta["group"],
        group_name=meta["group_name"],
        function=meta["function"],
        is_bicell=meta["is_bicell"],
        known=known,
        operands=operands,
        value=" ".join(values),
        indirect_byte=cell[6] if size == 8 else None,
        extra_byte=cell[7] if size == 8 else None,
        timer_interval_ms=TIMER_INTERVAL_MS.get(opcode),
    )


def _parse_code_row(
    row_data: bytes,
    record_index: int,
    row_index: int,
    encoding: str,
    issues: list[dict[str, Any]],
) -> Row:
    cells: list[Cell] = []
    offset = 0
    while offset < 40:
        cell = _parse_cell(
            row_data, offset, record_index, row_index, encoding, issues
        )
        cells.append(cell)
        offset += cell.size
    return Row(
        index=row_index,
        record_index=record_index,
        kind="code",
        raw_hex=row_data.hex(" ").upper(),
        cells=cells,
    )


def _read_comment(
    records: list[bytes],
    start_index: int,
    encoding: str,
    issues: list[dict[str, Any]],
) -> tuple[CommentBlock, int]:
    header = records[start_index]
    length = header[2]
    comment_type = header[3]
    output = bytearray()
    record_index = start_index
    current = header
    position = 6

    while len(output) < length:
        output.append(current[position])
        position = position + 1 if position % 4 != 3 else position + 3
        if len(output) < length and position >= 40:
            record_index += 1
            if record_index >= len(records):
                issues.append({
                    "record": start_index,
                    "message": f"comment length {length} exceeds available records",
                })
                break
            current = records[record_index]
            position = 2

    raw = bytes(output)
    text = _decode(raw, encoding).rstrip("\x00")
    version: int | None = None
    fields: list[str] | None = None

    if comment_type == 2:
        first_field = text.split("\t", 1)[0]
        try:
            version = int(first_field)
        except ValueError:
            issues.append({
                "record": start_index,
                "message": f"version comment is not an integer: {first_field!r}",
            })
    elif comment_type == 4:
        fields = [part.strip() for part in text.split("\t")]
        if len(fields) < 3:
            issues.append({
                "record": start_index,
                "message": "variable comment has fewer than three tab-separated fields",
            })
            fields.extend([""] * (3 - len(fields)))

    comment = CommentBlock(
        record_index=start_index,
        record_count=record_index - start_index + 1,
        comment_type=comment_type,
        length=length,
        text=text,
        raw_hex=header.hex(" ").upper(),
        version=version,
        fields=fields,
    )
    return comment, record_index + 1


def parse_lad(
    path: Path,
    encoding: str = "big5",
    sidecar_path: Path | None = None,
) -> dict[str, Any]:
    data = path.read_bytes()
    issues: list[dict[str, Any]] = []

    trailer_ok = len(data) >= 2 and data[-2:] == b"\x00\xFE"
    if trailer_ok:
        body = data[:-2]
    elif len(data) % 40 == 0:
        body = data
        issues.append({"message": "file has no 00 FE trailer"})
    else:
        raise LadFormatError(
            f"invalid size {len(data)}: expected 40-byte records plus 00 FE trailer"
        )
    if len(body) % 40:
        raise LadFormatError(
            f"invalid body size {len(body)}: not a multiple of 40"
        )

    records = [
        body[offset:offset + 40]
        for offset in range(0, len(body), 40)
    ]
    rows: list[Row] = []
    comments: list[CommentBlock] = []
    record_index = 0

    while record_index < len(records):
        record = records[record_index]
        row_index = len(rows)
        if record[1] == 0 and record[3] != 0:
            comment, next_record = _read_comment(
                records, record_index, encoding, issues
            )
            comments.append(comment)
            rows.append(Row(
                index=row_index,
                record_index=record_index,
                kind="comment",
                raw_hex=record.hex(" ").upper(),
                comment=comment,
            ))
            record_index = next_record
        else:
            rows.append(_parse_code_row(
                record, record_index, row_index, encoding, issues
            ))
            record_index += 1

    comment_counts = Counter(comment.comment_type for comment in comments)
    opcode_counts: Counter[int] = Counter()
    connection_counts: Counter[str] = Counter()
    cell_count = 0
    instruction_cell_count = 0
    bicell_count = 0
    labels: list[dict[str, Any]] = []

    for row in rows:
        if row.kind != "code" or row.cells is None:
            continue
        for cell in row.cells:
            cell_count += 1
            if cell.is_bicell:
                bicell_count += 1
            if cell.opcode not in (0, 13):
                instruction_cell_count += 1
            opcode_counts[cell.opcode] += 1
            for flag in cell.connection_flags:
                connection_counts[flag] += 1
            if cell.opcode == 213:
                labels.append({
                    "row": row.index,
                    "record": row.record_index,
                    "column": cell.column,
                    "name": cell.value,
                })

    variable_comments = [
        {
            "record": comment.record_index,
            "name": comment.fields[0] if comment.fields else "",
            "short": comment.fields[1] if comment.fields and len(comment.fields) > 1 else "",
            "long": comment.fields[2] if comment.fields and len(comment.fields) > 2 else "",
        }
        for comment in comments
        if comment.comment_type == 4
    ]

    sidecar_comments = parse_lc(sidecar_path) if sidecar_path and sidecar_path.exists() else []
    version = next(
        (comment.version for comment in comments
         if comment.comment_type == 2 and comment.version is not None),
        None,
    )
    opcode_histogram = {}
    for opcode, count in sorted(opcode_counts.items()):
        name = OPCODE_INFO.get(opcode, (f"OP{opcode:02X}", "未识别指令"))[0]
        opcode_histogram[f"0x{opcode:02X}"] = {
            "count": count,
            "mnemonic": name,
        }

    return {
        "format": "Syntec MLCEdit ladder",
        "tool": {
            "name": "lad_parser.py",
            "version": TOOL_VERSION,
        },
        "source": {
            "path": str(path.resolve()),
            "size": len(data),
            "sha256": _sha256(data),
            "encoding": encoding,
            "trailer": data[-2:].hex(" ").upper(),
        },
        "integrity": {
            "trailer_ok": trailer_ok,
            "physical_records": len(records),
            "logical_rows": len(rows),
            "issues": issues,
        },
        "version": version,
        "statistics": {
            "comments": len(comments),
            "comment_types": {
                str(kind): {
                    "name": COMMENT_TYPE_NAMES.get(kind, "unknown"),
                    "count": count,
                }
                for kind, count in sorted(comment_counts.items())
            },
            "code_rows": sum(1 for row in rows if row.kind == "code"),
            "cells": cell_count,
            "instruction_cells": instruction_cell_count,
            "bicells": bicell_count,
            "connections": dict(sorted(connection_counts.items())),
            "opcodes": opcode_histogram,
            "labels": len(labels),
            "variable_comments": len(variable_comments),
        },
        "labels": labels,
        "variable_comments": variable_comments,
        "sidecar_comments": sidecar_comments,
        "comments": [
            {
                "record": comment.record_index,
                "record_count": comment.record_count,
                "type": comment.comment_type,
                "type_name": COMMENT_TYPE_NAMES.get(comment.comment_type, "unknown"),
                "length": comment.length,
                "text": comment.text,
                "version": comment.version,
                "fields": comment.fields,
                "raw_header": comment.raw_hex,
            }
            for comment in comments
        ],
        "rows": [
            {
                "index": row.index,
                "record": row.record_index,
                "kind": row.kind,
                "raw_hex": row.raw_hex,
                "comment": (
                    {
                        "type": row.comment.comment_type,
                        "type_name": COMMENT_TYPE_NAMES.get(row.comment.comment_type, "unknown"),
                        "length": row.comment.length,
                        "text": row.comment.text,
                        "version": row.comment.version,
                        "fields": row.comment.fields,
                    }
                    if row.comment else None
                ),
                "cells": [
                    {
                        "column": cell.column,
                        "byte_offset": cell.byte_offset,
                        "size": cell.size,
                        "raw_hex": cell.raw_hex,
                        "connection": cell.connection,
                        "connection_flags": cell.connection_flags,
                        "opcode": cell.opcode,
                        "opcode_hex": f"0x{cell.opcode:02X}",
                        "mnemonic": cell.mnemonic,
                        "description": cell.description,
                        "group": cell.group,
                        "group_name": cell.group_name,
                        "function": cell.function,
                        "is_bicell": cell.is_bicell,
                        "known": cell.known,
                        "operands": cell.operands,
                        "value": cell.value,
                        "indirect_byte": cell.indirect_byte,
                        "extra_byte": cell.extra_byte,
                        "timer_interval_ms": cell.timer_interval_ms,
                    }
                    for cell in (row.cells or [])
                ] if row.kind == "code" else [],
            }
            for row in rows
        ],
    }


def parse_lc(path: Path) -> list[dict[str, str]]:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("big5", errors="replace")

    entries: list[dict[str, str]] = []
    angle_pattern = re.compile(r"<([^<>]*)><([^<>]*)><([^<>]*)>")
    for line in text.splitlines():
        match = angle_pattern.search(line)
        if match:
            entries.append({
                "name": match.group(1),
                "short": match.group(2),
                "long": match.group(3),
                "format": "angle",
            })
            continue
        if "\t" in line:
            fields = line.split("\t", 2)
            if len(fields) == 3 and fields[0].strip():
                entries.append({
                    "name": fields[0].strip(),
                    "short": fields[1].strip(),
                    "long": fields[2].strip(),
                    "format": "tab",
                })
    return entries


def _format_cell_detail(cell: Cell) -> str:
    flags = ",".join(cell.connection_flags) if cell.connection_flags else "--"
    parts = [
        f"c{cell.column:02d}",
        f"[{flags}]",
        f"{cell.opcode:02X}",
        cell.mnemonic,
    ]
    if cell.value:
        parts.append(cell.value)
    if cell.timer_interval_ms is not None:
        parts.append(f"({cell.timer_interval_ms}ms)")
    return " ".join(parts)


def _row_chart(row: Row) -> str:
    chart = [" "] * 10
    if row.kind != "code" or row.cells is None:
        return "         "
    for cell in row.cells:
        column = cell.column
        if column >= 10:
            continue
        if cell.opcode == 13:
            chart[column] = "="
        elif cell.opcode == 0:
            chart[column] = "."
        else:
            chart[column] = GROUP_CHARS.get(cell.group, "?")
        if cell.is_bicell and column + 1 < 10:
            chart[column + 1] = ">"
    return "".join(chart)


def render_disassembly(result: dict[str, Any]) -> str:
    source = result["source"]
    integrity = result["integrity"]
    stats = result["statistics"]
    lines = [
        "SYNTEC MLCEdit Ladder Disassembly",
        f"Source        : {source['path']}",
        f"Size / SHA256 : {source['size']} bytes / {source['sha256']}",
        f"Encoding      : {source['encoding']}",
        f"Records       : {integrity['physical_records']} physical, "
        f"{integrity['logical_rows']} logical rows",
        f"Version       : {result['version'] if result['version'] is not None else 'unknown'}",
        f"Comments      : {stats['comments']} "
        f"({', '.join(f'{key}:{value['count']}' for key, value in stats['comment_types'].items())})",
        f"Instructions  : {stats['instruction_cells']} cells, "
        f"{stats['bicells']} two-cell instructions",
        f"Issues        : {len(integrity['issues'])}",
        "",
        "Legend: . empty  = horizontal line  C contact  O coil  F function  T timer",
        "        N counter  J flow  A axis  G coord  L tool  K keyscan  S RS232",
        "        Z file system  X external  > second slot of a two-cell instruction",
        "Links : RU right-up, RD right-down, LU left-up, LD left-down",
        "",
    ]

    for row in result["rows"]:
        prefix = f"{row['index']:04d}  r{row['record']:04d}  "
        if row["kind"] == "comment":
            comment = row["comment"]
            text = _one_line(comment["text"])
            lines.append(
                f"{prefix}COMMENT type={comment['type']} "
                f"({comment['type_name']}) len={comment['length']} | {text}"
            )
            continue

        details = []
        for cell in row["cells"]:
            if (
                cell["opcode"] not in (0, 13)
                or cell["connection"]
                or not cell["known"]
            ):
                detail_cell = Cell(
                    column=cell["column"],
                    byte_offset=cell["byte_offset"],
                    size=cell["size"],
                    raw_hex=cell["raw_hex"],
                    connection=cell["connection"],
                    connection_flags=cell["connection_flags"],
                    opcode=cell["opcode"],
                    mnemonic=cell["mnemonic"],
                    description=cell["description"],
                    group=cell["group"],
                    group_name=cell["group_name"],
                    function=cell["function"],
                    is_bicell=cell["is_bicell"],
                    known=cell["known"],
                    operands=cell["operands"],
                    value=cell["value"],
                    indirect_byte=cell["indirect_byte"],
                    extra_byte=cell["extra_byte"],
                    timer_interval_ms=cell["timer_interval_ms"],
                )
                details.append(_format_cell_detail(detail_cell))
        suffix = f" | {' | '.join(details)}" if details else ""
        row_view = Row(
            index=row["index"],
            record_index=row["record"],
            kind=row["kind"],
            raw_hex=row["raw_hex"],
            cells=[
                Cell(
                    column=cell["column"],
                    byte_offset=cell["byte_offset"],
                    size=cell["size"],
                    raw_hex=cell["raw_hex"],
                    connection=cell["connection"],
                    connection_flags=cell["connection_flags"],
                    opcode=cell["opcode"],
                    mnemonic=cell["mnemonic"],
                    description=cell["description"],
                    group=cell["group"],
                    group_name=cell["group_name"],
                    function=cell["function"],
                    is_bicell=cell["is_bicell"],
                    known=cell["known"],
                    operands=cell["operands"],
                    value=cell["value"],
                    indirect_byte=cell["indirect_byte"],
                    extra_byte=cell["extra_byte"],
                    timer_interval_ms=cell["timer_interval_ms"],
                )
                for cell in row["cells"]
            ],
        )
        lines.append(f"{prefix}{_row_chart(row_view)}{suffix}")

    if result["integrity"]["issues"]:
        lines.extend(["", "ISSUES"])
        for issue in result["integrity"]["issues"]:
            lines.append(json.dumps(issue, ensure_ascii=False))

    return "\n".join(lines) + "\n"


def _default_paths(path: Path, output_dir: Path | None) -> tuple[Path, Path]:
    directory = output_dir if output_dir is not None else path.parent
    return (
        directory / f"{path.name}.json",
        directory / f"{path.name}.disasm.txt",
    )


def _find_sidecar(path: Path) -> Path | None:
    for suffix in (".lc", ".LC", ".Lc"):
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse and disassemble Syntec MLCEdit .lad ladder files."
    )
    parser.add_argument("lad", type=Path, help="input .lad file")
    parser.add_argument(
        "-e", "--encoding", default="big5",
        help="comment/string encoding (default: big5; use gb18030 for Simplified Chinese)",
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path,
        help="directory for <name>.json and <name>.disasm.txt",
    )
    parser.add_argument("--json", type=Path, help="explicit JSON output path")
    parser.add_argument(
        "--disasm", type=Path, help="explicit disassembly output path"
    )
    parser.add_argument("--lc", type=Path, help="optional legacy .LC sidecar")
    parser.add_argument(
        "--no-write", action="store_true",
        help="parse and print a summary without writing output files",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="do not print the summary"
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")

    try:
        result = parse_lad(
            args.lad,
            encoding=args.encoding,
            sidecar_path=args.lc or _find_sidecar(args.lad),
        )
    except (OSError, LadFormatError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.no_write:
        default_json, default_disasm = _default_paths(args.lad, args.output_dir)
        json_path = args.json or default_json
        disasm_path = args.disasm or default_disasm
        json_path.parent.mkdir(parents=True, exist_ok=True)
        disasm_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        disasm_path.write_text(render_disassembly(result), encoding="utf-8")
    else:
        json_path = None
        disasm_path = None

    if not args.quiet:
        stats = result["statistics"]
        integrity = result["integrity"]
        print(
            f"{args.lad.name}: {integrity['physical_records']} records, "
            f"{integrity['logical_rows']} logical rows, "
            f"{stats['comments']} comments, "
            f"{stats['instruction_cells']} instructions, "
            f"{len(integrity['issues'])} issues"
        )
        print(f"SHA256: {result['source']['sha256']}")
        if json_path is not None:
            print(f"JSON: {json_path.resolve()}")
        if disasm_path is not None:
            print(f"Disassembly: {disasm_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
