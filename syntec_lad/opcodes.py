"""Syntec PLC opcode metadata and operand encoding.

The 16-bit ``PLCOP_MAP`` table is copied from the recovered MLCEdit
``plc.plcop_map`` implementation.  Raw opcode bytes are converted through this
table into group/function/operand metadata.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from .errors import LadFormatError, LadUnsupportedError, LadValidationError


PLCOP_MAP: tuple[int, ...] = (
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
    "Null",
    "Contact",
    "Coil",
    "Function",
    "Timer",
    "Counter",
    "Flow",
    "HorizontalLink",
    "Axis",
    "Coord",
    "Tool",
    "Keyscan",
    "RS232",
    "FileSys",
    "External",
)

CONNECTION_RU = 0x01
CONNECTION_RD = 0x02
CONNECTION_LU = 0x04
CONNECTION_LD = 0x08

OPCODE_NAMES: dict[int, str] = {
    0x00: "NOP",
    0x01: "LD",
    0x03: "LDI",
    0x05: "PLS",
    0x07: "PLF",
    0x09: "LDT",
    0x0A: "LDTI",
    0x0B: "LDC",
    0x0C: "LDCI",
    0x0D: "LINE",
    0x0E: "OUT",
    0x10: "OUTI",
    0x12: "SET",
    0x14: "RST",
    0x21: "ADD",
    0x27: "ADD",
    0x33: "SUB",
    0x39: "SUB",
    0x45: "MUL",
    0x4B: "MUL",
    0x57: "DIV",
    0x5D: "DIV",
    0x5E: "CMPG",
    0x62: "CMPL",
    0x66: "CMPE",
    0x6A: "CMPG",
    0x6C: "CMPL",
    0x6E: "CMPE",
    0x70: "MOV",
    0x72: "MOV",
    0x81: "AND",
    0x87: "AND",
    0x93: "OR",
    0x99: "OR",
    0xA5: "XOR",
    0xAB: "XOR",
    0xC2: "TIM",
    0xC3: "TIM",
    0xC4: "TIM",
    0xC5: "TIM",
    0xC6: "CNTUP",
    0xC7: "CNTDOWN",
    0xC9: "CNTRESET",
    0xCA: "JMP",
    0xCB: "JSR",
    0xCF: "END",
    0xD0: "RTS",
    0xD4: "MSG",
    0xD5: "LAB",
    0xE0: "CNTRINGUP",
    0xE1: "CNTRINGDOWN",
    0xE2: "TIM",
    0xE3: "TIM",
    0xE4: "TIM",
    0xE5: "CNTUP",
    0xE6: "CNTDOWN",
    0xE7: "TIM",
    0xE9: "CNTRINGUP",
    0xEA: "CNTRINGDOWN",
    0xFD: "FEND",
}

STRUCTURAL_OPCODES = {0xCF: "END", 0xD0: "RTS", 0xD5: "LAB", 0xFD: "FEND"}
COIL_OPCODES = {
    "OUT": 0x0E,
    "OUTI": 0x10,
    "PLS": 0x05,
    "PLF": 0x07,
    "SET": 0x12,
    "RST": 0x14,
}

CONTACT_OPCODES = {
    ("normal", "open"): 0x01,
    ("normal", "closed"): 0x03,
    ("timer", "open"): 0x09,
    ("timer", "closed"): 0x0A,
    ("counter", "open"): 0x0B,
    ("counter", "closed"): 0x0C,
}

TIMER_CONST_OPCODES = {
    "1ms": 0xC2,
    "10ms": 0xC3,
    "100ms": 0xC4,
    "1s": 0xC5,
}
TIMER_REGISTER_OPCODES = {
    "1ms": 0xE7,
    "10ms": 0xE2,
    "100ms": 0xE3,
    "1s": 0xE4,
}

MATH_OPCODES = {
    ("ADD", "const"): 0x27,
    ("ADD", "register"): 0x21,
    ("SUB", "const"): 0x39,
    ("SUB", "register"): 0x33,
    ("MUL", "const"): 0x4B,
    ("MUL", "register"): 0x45,
    ("DIV", "const"): 0x5D,
    ("DIV", "register"): 0x57,
    ("MOV", "const"): 0x70,
    ("MOV", "register"): 0x72,
    ("AND", "const"): 0x87,
    ("AND", "register"): 0x81,
    ("OR", "const"): 0x99,
    ("OR", "register"): 0x93,
    ("XOR", "const"): 0xAB,
    ("XOR", "register"): 0xA5,
}

COMPARE_OPCODES = {
    (">", "const"): 0x6A,
    (">", "register"): 0x5E,
    ("<", "const"): 0x6C,
    ("<", "register"): 0x62,
    ("=", "const"): 0x6E,
    ("=", "register"): 0x66,
}

COUNTER_OPCODES = {
    ("CNTUP", "const"): 0xC6,
    ("CNTUP", "register"): 0xE5,
    ("CNTDOWN", "const"): 0xC7,
    ("CNTDOWN", "register"): 0xE6,
    ("CNTRESET", "const"): 0xC9,
    ("CNTRINGUP", "const"): 0xE0,
    ("CNTRINGUP", "register"): 0xE9,
    ("CNTRINGDOWN", "const"): 0xE1,
    ("CNTRINGDOWN", "register"): 0xEA,
}

FLOW_OPCODES = {"JMP": 0xCA, "JSR": 0xCB, "MSG": 0xD4}

OPERAND_NULL = -1
OPERAND_PLC_BITS = 0
OPERAND_TIMER = 1
OPERAND_COUNTER = 2
OPERAND_REGISTER = 3
OPERAND_CONST = 4
OPERAND_STRING = 5
OPERAND_DEVICE_LEAD = 6

OPERAND_TYPE_NAMES = {
    OPERAND_PLC_BITS: "PLCBits",
    OPERAND_TIMER: "Timer",
    OPERAND_COUNTER: "Counter",
    OPERAND_REGISTER: "Register",
    OPERAND_CONST: "ConstValue",
    OPERAND_STRING: "String",
    OPERAND_DEVICE_LEAD: "DeviceNameLead",
}

_PLC_BITS_RE = re.compile(r"^([IOCSA])(\d{1,3})$")
_REGISTER_BIT_RE = re.compile(r"^R(\d{1,5})\.(\d{1,2})$")
_TIMER_RE = re.compile(r"^TM(\d{1,3})$")
_COUNTER_RE = re.compile(r"^CT(\d{1,3})$")
_REGISTER_RE = re.compile(r"^(@)?R(\d{1,5})$")
_CONST_RE = re.compile(r"^#(-?\d{1,6})$")


@dataclass(frozen=True)
class OpcodeMeta:
    mapped: int
    group: int
    group_name: str
    function: int
    is_bicell: bool
    op1_type: int
    op2_type: int


def opcode_meta(raw_opcode: int) -> OpcodeMeta:
    if not 0 <= raw_opcode <= 255:
        raise LadFormatError(f"opcode out of range: {raw_opcode}")
    mapped = PLCOP_MAP[raw_opcode]
    return OpcodeMeta(
        mapped=mapped,
        group=mapped & 0xF,
        group_name=GROUP_NAMES[mapped & 0xF],
        function=(mapped & 0x1F0) >> 4,
        is_bicell=bool(mapped & 0x200),
        op1_type=(mapped & 0x1C00) >> 10,
        op2_type=(mapped & 0xE000) >> 13,
    )


def opcode_name(raw_opcode: int) -> str:
    return OPCODE_NAMES.get(raw_opcode, f"OP{raw_opcode:02X}")


def is_structural_row(row: bytes) -> bool:
    if len(row) != 40:
        return False
    return row[1] in STRUCTURAL_OPCODES


def is_blank_row(row: bytes) -> bool:
    return len(row) == 40 and not any(row)


def is_comment_record(record: bytes) -> bool:
    return len(record) == 40 and record[1] == 0 and record[3] != 0


@dataclass(frozen=True)
class EncodedOperand:
    kind: str
    raw: int | None = None
    indirect: bool = False
    text: str | None = None


def parse_plc_device(value: str) -> EncodedOperand:
    match = _PLC_BITS_RE.fullmatch(value.upper())
    if match:
        kind = "IOCSA".index(match.group(1))
        index = int(match.group(2))
        if index > 511:
            raise LadValidationError(f"PLC bit index out of range: {value}")
        raw = (kind << 9) | index
        return EncodedOperand("plc_bits", raw=raw)
    match = _REGISTER_BIT_RE.fullmatch(value.upper())
    if match:
        register = int(match.group(1))
        bit = int(match.group(2))
        if register > 639 or bit > 31:
            raise LadValidationError(f"register bit out of range: {value}")
        low12 = ((register % 128) << 5) | bit
        upper = (register // 128) + 1
        return EncodedOperand("plc_bits", raw=(upper << 12) | low12)
    raise LadValidationError(
        f"invalid PLC bit '{value}'; expected I000/O000/C000/S000/A000 or R0000.00"
    )


def parse_contact_device(value: str) -> tuple[str, EncodedOperand]:
    upper = value.upper()
    if _TIMER_RE.fullmatch(upper):
        return "timer", parse_timer(upper)
    if _COUNTER_RE.fullmatch(upper):
        return "counter", parse_counter(upper)
    return "normal", parse_plc_device(upper)


def parse_timer(value: str) -> EncodedOperand:
    match = _TIMER_RE.fullmatch(value.upper())
    if not match:
        raise LadValidationError(f"invalid timer '{value}'; expected TM000")
    number = int(match.group(1))
    if number > 255:
        raise LadValidationError(f"timer index out of range: {value}")
    return EncodedOperand("timer", raw=number)


def parse_counter(value: str) -> EncodedOperand:
    match = _COUNTER_RE.fullmatch(value.upper())
    if not match:
        raise LadValidationError(f"invalid counter '{value}'; expected CT000")
    number = int(match.group(1))
    if number > 255:
        raise LadValidationError(f"counter index out of range: {value}")
    return EncodedOperand("counter", raw=number)


def parse_register(value: str, allow_indirect: bool = True) -> EncodedOperand:
    match = _REGISTER_RE.fullmatch(value.upper())
    if not match:
        raise LadValidationError(f"invalid register '{value}'; expected R0000")
    indirect = bool(match.group(1))
    if indirect and not allow_indirect:
        raise LadValidationError(f"indirect register not allowed here: {value}")
    number = int(match.group(2))
    if number > 65535:
        raise LadValidationError(f"register index out of range: {value}")
    return EncodedOperand("register", raw=number, indirect=indirect)


def parse_const(value: str) -> EncodedOperand:
    match = _CONST_RE.fullmatch(value.strip())
    if not match:
        raise LadValidationError(f"invalid constant '{value}'; expected #-32768..#32767")
    number = int(match.group(1))
    if not -32768 <= number <= 32767:
        raise LadValidationError(f"constant out of signed 16-bit range: {value}")
    return EncodedOperand("const", raw=number)


def classify_value(value: str, *, second_register_only: bool = False) -> EncodedOperand:
    stripped = value.strip()
    if stripped.startswith("#"):
        if second_register_only:
            raise LadValidationError(
                f"second operand must be a register, got constant: {value}"
            )
        return parse_const(stripped)
    return parse_register(stripped)


def decode_plc_bits(raw: int) -> str:
    low12 = raw & 0xFFF
    upper = raw >> 12
    if upper == 0:
        index = low12 & 0x1FF
        kind = low12 >> 9
        if kind <= 4:
            return f"{'IOCSA'[kind]}{index:03d}"
        return f"BIT?{raw:04X}"
    if 1 <= upper <= 5:
        register = (low12 >> 5) + (upper - 1) * 128
        bit = low12 & 0x1F
        return f"R{register:04d}.{bit:02d}"
    return f"BIT?{raw:04X}"


def _write_operand(
    buf: bytearray,
    offset: int,
    operand: EncodedOperand,
    kind: str,
) -> None:
    if offset + 1 >= len(buf):
        raise LadFormatError(f"operand offset {offset} exceeds cell width")
    if kind in ("timer", "counter"):
        if operand.kind != kind:
            raise LadValidationError(f"expected {kind} operand")
        struct.pack_into("<H", buf, offset, int(operand.raw or 0))
    elif kind == "register":
        if operand.kind != "register":
            raise LadValidationError("expected register operand")
        struct.pack_into("<H", buf, offset, int(operand.raw or 0))
    elif kind == "const":
        if operand.kind != "const":
            raise LadValidationError("expected constant operand")
        struct.pack_into("<h", buf, offset, int(operand.raw or 0))
    elif kind == "plc_bits":
        if operand.kind != "plc_bits":
            raise LadValidationError("expected PLC bit operand")
        struct.pack_into("<H", buf, offset, int(operand.raw or 0))
    else:
        raise LadUnsupportedError(f"unsupported operand kind: {kind}")


def encode_instruction_cell(
    raw_opcode: int,
    *,
    op1: EncodedOperand | None = None,
    op2: EncodedOperand | None = None,
    string: str | None = None,
    encoding: str = "gb2312",
    connection: int = 0,
) -> bytes:
    """Encode one instruction occupying one or two four-byte slots."""

    meta = opcode_meta(raw_opcode)
    width = 8 if meta.is_bicell else 4
    buf = bytearray(width)
    buf[0] = connection & 0x0F
    buf[1] = raw_opcode

    if raw_opcode in FLOW_OPCODES.values() or raw_opcode in STRUCTURAL_OPCODES:
        if string is None:
            string = ""
        try:
            encoded = string.encode(encoding)
        except UnicodeEncodeError as exc:
            raise LadValidationError(
                f"string {string!r} cannot be encoded as {encoding}: {exc}"
            ) from exc
        if len(encoded) > 6:
            raise LadValidationError(
                f"flow string {string!r} is {len(encoded)} bytes; maximum is 6"
            )
        buf[2:8] = encoded.ljust(6, b"\0")
        return bytes(buf)

    indirect_flags = 0
    if meta.op1_type == OPERAND_REGISTER and op1 is not None:
        _write_operand(buf, 2, op1, "register")
        indirect_flags |= 0x02 if op1.indirect else 0
    elif meta.op1_type == OPERAND_CONST and op1 is not None:
        _write_operand(buf, 2, op1, "const")
    elif meta.op1_type == OPERAND_PLC_BITS and op1 is not None:
        _write_operand(buf, 2, op1, "plc_bits")
    elif meta.op1_type == OPERAND_TIMER and op1 is not None:
        _write_operand(buf, 2, op1, "timer")
    elif meta.op1_type == OPERAND_COUNTER and op1 is not None:
        _write_operand(buf, 2, op1, "counter")
    elif op1 is not None:
        raise LadUnsupportedError(
            f"opcode 0x{raw_opcode:02X} does not accept the supplied first operand"
        )

    if width == 8:
        if meta.op2_type == OPERAND_REGISTER and op2 is not None:
            _write_operand(buf, 4, op2, "register")
            indirect_flags |= 0x01 if op2.indirect else 0
        elif meta.op2_type == OPERAND_CONST and op2 is not None:
            _write_operand(buf, 4, op2, "const")
        elif meta.op2_type == OPERAND_PLC_BITS and op2 is not None:
            _write_operand(buf, 4, op2, "plc_bits")
        elif meta.op2_type == OPERAND_TIMER and op2 is not None:
            _write_operand(buf, 4, op2, "timer")
        elif meta.op2_type == OPERAND_COUNTER and op2 is not None:
            _write_operand(buf, 4, op2, "counter")
        elif meta.op2_type == OPERAND_STRING:
            pass
        elif meta.op2_type not in (OPERAND_NULL, OPERAND_STRING) and op2 is not None:
            raise LadUnsupportedError(
                f"opcode 0x{raw_opcode:02X} does not accept the supplied second operand"
            )
        buf[6] = indirect_flags
    elif indirect_flags:
        raise LadUnsupportedError("single-cell instructions cannot hold indirect registers")

    return bytes(buf)


def make_line_cell(connection: int = 0) -> bytes:
    return bytes((connection & 0x0F, 0x0D, 0x00, 0x00))


def make_nop_cell(connection: int = 0) -> bytes:
    return bytes((connection & 0x0F, 0x00, 0x00, 0x00))


def make_structural_row(raw_opcode: int, text: str = "", encoding: str = "gb2312") -> bytes:
    """Create LAB/RTS/END/FEND rows in the same shape as MLCEdit."""

    if raw_opcode not in STRUCTURAL_OPCODES:
        raise LadUnsupportedError(f"not a structural opcode: 0x{raw_opcode:02X}")
    row = bytearray(40)
    first = bytearray(8)
    first[1] = raw_opcode
    if text:
        encoded = text.encode(encoding)
        if len(encoded) > 6:
            raise LadValidationError(f"label {text!r} exceeds six encoded bytes")
        first[2:8] = encoded.ljust(6, b"\0")
    row[0:8] = first
    # MLCEdit's InsertRTSLine fills slot 1 as well as slots 2..9. LAB and
    # END/FEND start filling at slot 2.  The first slot of a two-cell flow
    # instruction is always the opcode slot.
    start = 1 if raw_opcode == 0xD0 else 2
    for slot in range(start, 10):
        row[slot * 4 + 1] = 0x0D
    return bytes(row)
