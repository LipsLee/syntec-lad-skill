"""Deterministic compiler from the high-level ladder plan to .lad records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import LadUnsupportedError, LadValidationError
from .model import LadDocument, encode_comment
from .opcodes import (
    COIL_OPCODES,
    COMPARE_OPCODES,
    CONTACT_OPCODES,
    COUNTER_OPCODES,
    FLOW_OPCODES,
    MATH_OPCODES,
    TIMER_CONST_OPCODES,
    TIMER_REGISTER_OPCODES,
    EncodedOperand,
    classify_value,
    encode_instruction_cell,
    make_line_cell,
    make_nop_cell,
    make_structural_row,
    parse_contact_device,
    parse_counter,
    parse_plc_device,
    parse_register,
    parse_timer,
)


PLAN_SCHEMA = "syntec-lad-plan/v1"
PROGRAM_NAME_RE = re.compile(r"^[A-Za-z0-9]{1,6}$")
FLOW_STRING_RE = re.compile(r"^[A-Za-z0-9_]{1,6}$")


@dataclass(frozen=True)
class Leaf:
    kind: str
    opcode: int
    width: int
    op1: EncodedOperand | None
    op2: EncodedOperand | None
    source: str | None = None

    @property
    def key(self) -> tuple[object, ...]:
        def operand_key(value: EncodedOperand | None) -> tuple[object, ...] | None:
            if value is None:
                return None
            return (value.kind, value.raw, value.indirect, value.text)

        return (
            self.kind,
            self.opcode,
            self.width,
            operand_key(self.op1),
            operand_key(self.op2),
            self.source,
        )


@dataclass
class Action:
    op: str
    opcode: int
    op1: EncodedOperand | None
    op2: EncodedOperand | None
    string: str | None = None
    action_slot: int = 9
    width: int = 1


@dataclass
class UnitPlacement:
    start: int
    width: int
    data: bytearray
    kind: str

    def left_flags(self, flags: int) -> None:
        self.data[0] |= flags & 0x0F

    def right_flags(self, flags: int) -> None:
        # The first 4-byte slot carries both left and right flags for bicells.
        self.data[0] |= flags & 0x0F

    def to_bytes(self) -> bytes:
        return bytes(self.data)


class RowLayout:
    def __init__(self) -> None:
        self.units: list[UnitPlacement] = []
        self.by_start: dict[int, UnitPlacement] = {}
        self.by_end: dict[int, UnitPlacement] = {}
        self.occupied: list[bool] = [False] * 10

    def place(
        self,
        start: int,
        data: bytes | bytearray,
        kind: str,
    ) -> UnitPlacement:
        width = len(data) // 4
        if width not in (1, 2):
            raise LadValidationError(f"invalid cell width: {len(data)} bytes")
        if start < 0 or start + width > 10:
            raise LadUnsupportedError(
                f"network cannot be placed within ten columns (slot {start}..{start + width - 1})"
            )
        if any(self.occupied[start:start + width]):
            raise LadUnsupportedError(
                f"network cell overlap at slot {start}..{start + width - 1}"
            )
        unit = UnitPlacement(start, width, bytearray(data), kind)
        self.units.append(unit)
        self.by_start[start] = unit
        self.by_end[start + width] = unit
        for slot in range(start, start + width):
            self.occupied[slot] = True
        return unit

    def ensure_connector(self, slot: int, kind: str = "NOP") -> UnitPlacement:
        unit = self.by_start.get(slot)
        if unit is not None:
            return unit
        data = make_line_cell() if kind == "LINE" else make_nop_cell()
        return self.place(slot, data, kind)

    def fill_lines(self, start: int, end: int) -> None:
        for slot in range(start, end + 1):
            if not self.occupied[slot]:
                self.place(slot, make_line_cell(), "LINE")

    def unit_ending_at(self, boundary: int) -> UnitPlacement | None:
        return self.by_end.get(boundary)

    def unit_starting_at(self, slot: int) -> UnitPlacement | None:
        return self.by_start.get(slot)

    def render(self) -> bytes:
        row = bytearray(40)
        for unit in self.units:
            offset = unit.start * 4
            row[offset:offset + len(unit.data)] = unit.data
        return bytes(row)


def _normalize_condition(condition: Any) -> list[list[Leaf]]:
    if condition is None:
        return [[]]
    if not isinstance(condition, dict):
        raise LadValidationError("network condition must be an object")
    allowed_keys = {"all", "any", "contact", "compare", "always"}
    if "contact" in condition:
        allowed_keys.add("normally")
    unknown_keys = set(condition) - allowed_keys
    if unknown_keys:
        raise LadValidationError(
            "unknown condition field(s): " + ", ".join(sorted(unknown_keys))
        )
    present_keys = [
        key for key in ("all", "any", "contact", "compare", "always")
        if key in condition
    ]
    if len(present_keys) != 1:
        raise LadValidationError(
            "condition must contain exactly one of all, any, contact, compare, or always"
        )
    if "all" in condition:
        values = condition["all"]
        if not isinstance(values, list):
            raise LadValidationError("condition.all must be an array")
        if not values:
            return [[]]
        result: list[list[Leaf]] = [[]]
        for value in values:
            child = _normalize_condition(value)
            result = [left + right for left in result for right in child]
        return _limit_dnf(result)
    if "any" in condition:
        values = condition["any"]
        if not isinstance(values, list) or not values:
            raise LadValidationError("condition.any must be a non-empty array")
        result: list[list[Leaf]] = []
        for value in values:
            result.extend(_normalize_condition(value))
        if any(not term for term in result):
            return [[]]
        return _limit_dnf(result)
    if "contact" in condition:
        device = str(condition["contact"])
        normally = str(condition.get("normally", "open")).lower()
        if normally not in ("open", "closed"):
            raise LadValidationError(
                f"contact.normally must be 'open' or 'closed': {normally!r}"
            )
        contact_kind, operand = parse_contact_device(device)
        opcode = CONTACT_OPCODES[(contact_kind, normally)]
        return [[Leaf("contact", opcode, 1, operand, None, source=device.upper())]]
    if "compare" in condition:
        compare = condition["compare"]
        if not isinstance(compare, dict):
            raise LadValidationError("condition.compare must be an object")
        if set(compare) - {"relation", "value", "other"}:
            raise LadValidationError(
                "condition.compare only accepts relation, value, and other"
            )
        relation = str(compare.get("relation", "="))
        relation = {
            ">": ">",
            "gt": ">",
            "<": "<",
            "lt": "<",
            "==": "=",
            "=": "=",
            "eq": "=",
        }.get(relation.lower(), relation)
        if relation not in (">", "<", "="):
            raise LadValidationError("compare.relation must be >, <, or =")
        value_text = str(compare.get("value", ""))
        other_text = str(compare.get("other", ""))
        value = classify_value(value_text, second_register_only=True)
        other = classify_value(other_text)
        raw_opcode = COMPARE_OPCODES[
            (relation, "const" if other.kind == "const" else "register")
        ]
        return [[Leaf("compare", raw_opcode, 2, other, value, source=relation)]]
    if "always" in condition:
        if not condition["always"]:
            raise LadValidationError("condition.always must be true when used")
        return [[]]
    raise LadValidationError(
        "condition must contain all, any, contact, compare, or always"
    )


def _limit_dnf(terms: list[list[Leaf]]) -> list[list[Leaf]]:
    if len(terms) > 64:
        raise LadUnsupportedError("condition expands to more than 64 parallel terms")
    if any(len(term) > 64 for term in terms):
        raise LadUnsupportedError("a parallel term contains more than 64 cells")
    return terms


def _parse_action(action: Any) -> Action:
    if not isinstance(action, dict):
        raise LadValidationError("each action must be an object")
    op = str(action.get("op", "")).upper()
    if op in COIL_OPCODES:
        operand = parse_plc_device(str(action.get("operand", "")))
        return Action(op, COIL_OPCODES[op], operand, None, action_slot=9, width=1)
    if op == "TIM":
        timer = parse_timer(str(action.get("timer", "")))
        unit = str(action.get("unit", "100ms")).lower().replace(" ", "")
        unit = {
            "0.001s": "1ms",
            "0.01s": "10ms",
            "0.1s": "100ms",
            "1000ms": "1s",
            "1.0s": "1s",
        }.get(unit, unit)
        if unit not in TIMER_CONST_OPCODES:
            raise LadValidationError("timer unit must be 1ms, 10ms, 100ms, or 1s")
        preset = classify_value(str(action.get("preset", "")))
        raw_opcode = (
            TIMER_CONST_OPCODES[unit]
            if preset.kind == "const"
            else TIMER_REGISTER_OPCODES[unit]
        )
        return Action(op, raw_opcode, timer, preset, action_slot=8, width=2)
    if op in ("CNTUP", "CNTDOWN", "CNTRESET", "CNTRINGUP", "CNTRINGDOWN"):
        counter = parse_counter(str(action.get("counter", "")))
        if op == "CNTRESET":
            if "preset" in action:
                preset = classify_value(str(action["preset"]))
                if preset.kind != "const" or preset.raw != 0:
                    raise LadValidationError(
                        "CNTRESET does not accept a non-zero preset"
                    )
            return Action(
                op,
                COUNTER_OPCODES[("CNTRESET", "const")],
                counter,
                None,
                action_slot=8,
                width=2,
            )
        preset_text = action.get("preset")
        if preset_text is None:
            raise LadValidationError(f"{op} requires a preset")
        else:
            preset = classify_value(str(preset_text))
        key = (op, "const" if preset.kind == "const" else "register")
        if key not in COUNTER_OPCODES:
            raise LadUnsupportedError(
                f"counter action {op} does not support {preset.kind} presets"
            )
        return Action(op, COUNTER_OPCODES[key], counter, preset, action_slot=8, width=2)
    if op in ("ADD", "SUB", "MUL", "DIV", "AND", "OR", "XOR"):
        source = classify_value(str(action.get("src", action.get("source", ""))))
        destination = parse_register(str(action.get("dst", action.get("destination", ""))))
        key = (op, "const" if source.kind == "const" else "register")
        return Action(op, MATH_OPCODES[key], source, destination, action_slot=8, width=2)
    if op == "MOV":
        source = classify_value(str(action.get("src", action.get("source", ""))))
        destination = parse_register(str(action.get("dst", action.get("destination", ""))))
        return Action(
            op,
            MATH_OPCODES[("MOV", "const" if source.kind == "const" else "register")],
            source,
            destination,
            action_slot=8,
            width=2,
        )
    if op in FLOW_OPCODES:
        target = action.get("target", action.get("text", ""))
        target_text = str(target)
        if op in ("JMP", "JSR"):
            target_text = target_text.upper()
        if not FLOW_STRING_RE.fullmatch(target_text):
            raise LadValidationError(
                f"{op} string must be 1-6 ASCII letters, digits, or underscores"
            )
        return Action(
            op,
            FLOW_OPCODES[op],
            None,
            None,
            string=target_text,
            action_slot=8,
            width=2,
        )
    raise LadUnsupportedError(f"unsupported action opcode: {op or '<missing>'}")


def _make_leaf_unit(leaf: Leaf, encoding: str) -> bytes:
    return encode_instruction_cell(
        leaf.opcode,
        op1=leaf.op1,
        op2=leaf.op2,
        encoding=encoding,
    )


def _make_action_unit(action: Action, encoding: str) -> bytes:
    return encode_instruction_cell(
        action.opcode,
        op1=action.op1,
        op2=action.op2,
        string=action.string,
        encoding=encoding,
    )


def _place_leaves(
    layout: RowLayout,
    leaves: list[Leaf],
    start: int,
    encoding: str,
) -> int:
    slot = start
    for leaf in leaves:
        if slot + leaf.width > 10:
            raise LadUnsupportedError(
                f"network is too wide: cannot place {leaf.kind} at column {slot}"
            )
        layout.place(slot, _make_leaf_unit(leaf, encoding), leaf.kind)
        slot += leaf.width
    return slot


def _common_prefix(terms: list[list[Leaf]]) -> int:
    if not terms:
        return 0
    length = 0
    while True:
        if any(length >= len(term) for term in terms):
            break
        key = terms[0][length].key
        if any(term[length].key != key for term in terms[1:]):
            break
        length += 1
    return length


def _compile_network_rows(
    network: dict[str, Any],
    *,
    encoding: str,
) -> list[bytes]:
    terms = _normalize_condition(network.get("condition"))
    actions_raw = network.get("actions")
    if not isinstance(actions_raw, list) or not actions_raw:
        raise LadValidationError("each network requires at least one action")
    actions = [_parse_action(value) for value in actions_raw]

    # A network in the plan means one conditional network.  If several actions
    # are supplied, emit one ladder row per action with the same condition.
    #
    # Each DNF term gets its own rung row.  The terms are expanded instead of
    # trying to guess a sharing point in the editor's implicit rung format:
    # this keeps the generated logic deterministic and makes every vertical
    # bus local to one column boundary.  A common prefix is therefore repeated
    # per branch, which is electrically equivalent to a shared prefix.
    all_rows: list[bytes] = []
    for action in actions:
        action_start = action.action_slot
        if action.width == 2 and action_start != 8:
            raise LadUnsupportedError("two-cell actions must start at column 8")

        branch_widths = [sum(leaf.width for leaf in term) for term in terms]
        max_width = max(branch_widths, default=0)
        if max_width > action_start:
            raise LadUnsupportedError(
                f"network needs {max_width} columns but action starts at column {action_start}"
            )

        if len(terms) == 1:
            layout = RowLayout()
            end = _place_leaves(layout, terms[0], 0, encoding)
            layout.fill_lines(end, action_start - 1)
            layout.place(action_start, _make_action_unit(action, encoding), "ACTION")
            all_rows.append(layout.render())
            continue

        if max_width < 1:
            # An OR containing a constant-true term is simply constant true.
            # _normalize_condition normally collapses this; keep the guard
            # here for callers that construct Leaf lists directly.
            raise LadUnsupportedError("parallel ladder branches need at least one cell")
        if max_width >= action_start:
            raise LadUnsupportedError(
                "parallel network must leave one column for the merge bus"
            )

        layouts: list[RowLayout] = []
        for index, term in enumerate(terms):
            layout = RowLayout()
            end = _place_leaves(layout, term, 0, encoding)
            # Pad every branch to the same merge boundary.  The top row then
            # carries the horizontal continuation to the action; lower rows
            # terminate with NOPs.  NOP is intentional here: a LINE on a lower
            # branch would create an extra horizontal path in the editor.
            if end < max_width:
                layout.fill_lines(end, max_width - 1)
            if index == 0:
                layout.fill_lines(max_width, action_start - 1)
            else:
                for slot in range(max_width, 10):
                    layout.place(slot, make_nop_cell(), "NOP")
            layouts.append(layout)

        if len(layouts) > 1:
            last = len(layouts) - 1
            for index, layout in enumerate(layouts):
                left = layout.unit_ending_at(max_width)
                right = layout.unit_starting_at(max_width)
                if left is None or right is None:
                    raise LadUnsupportedError("parallel branch has a missing merge cell")
                # Top: RD/LD, middle: RU|RD / LU|LD, bottom: RU/LU.
                left_flags = 0x02 if index < last else 0x01
                right_flags = 0x08 if index < last else 0x04
                if index > 0:
                    left_flags |= 0x01
                    right_flags |= 0x04
                if index < last:
                    left_flags |= 0x02
                    right_flags |= 0x08
                else:
                    left_flags &= ~0x02
                    right_flags &= ~0x08
                left.right_flags(left_flags)
                right.left_flags(right_flags)

        layouts[0].place(action_start, _make_action_unit(action, encoding), "ACTION")
        all_rows.extend(layout.render() for layout in layouts)

    return all_rows


def _validate_program_name(name: str) -> None:
    if not PROGRAM_NAME_RE.fullmatch(name):
        raise LadValidationError(
            f"program name must be 1-6 ASCII letters/digits: {name!r}"
        )


def _validate_flow_references(
    programs: list[dict[str, Any]],
    program_names: set[str],
) -> None:
    for program in programs:
        networks = program.get("networks", [])
        if not isinstance(networks, list):
            continue
        for network in networks:
            if not isinstance(network, dict):
                continue
            actions = network.get("actions", [])
            if not isinstance(actions, list):
                continue
            for action in actions:
                if not isinstance(action, dict):
                    continue
                op = str(action.get("op", "")).upper()
                if op not in ("JMP", "JSR"):
                    continue
                target = str(action.get("target", action.get("text", ""))).upper()
                if target not in program_names:
                    raise LadValidationError(
                        f"{op} target {target!r} does not name a declared subprogram"
                    )


def _build_records(plan: dict[str, Any], encoding: str) -> list[bytes]:
    programs = plan.get("programs")
    if not isinstance(programs, list) or not programs:
        raise LadValidationError("plan.programs must be a non-empty array")
    names: set[str] = set()
    normalized_programs: list[dict[str, Any]] = []
    for index, program in enumerate(programs):
        if not isinstance(program, dict):
            raise LadValidationError("each program must be an object")
        name = str(program.get("name", "MAIN" if index == 0 else "")).upper()
        if index == 0:
            if name not in ("MAIN",):
                raise LadValidationError("the first program must be named MAIN")
        else:
            _validate_program_name(name)
        if name in names:
            raise LadValidationError(f"duplicate program name: {name}")
        names.add(name)
        normalized_programs.append({**program, "name": name})

    _validate_flow_references(normalized_programs, names - {"MAIN"})

    records: list[bytes] = []
    main = normalized_programs[0]
    if main.get("comment"):
        records.extend(encode_comment(str(main["comment"]), 1, encoding))
    networks = main.get("networks", [])
    if not isinstance(networks, list):
        raise LadValidationError("program.networks must be an array")
    for network in networks:
        if not isinstance(network, dict):
            raise LadValidationError("each network must be an object")
        if network.get("comment"):
            records.extend(encode_comment(str(network["comment"]), 1, encoding))
        records.extend(_compile_network_rows(network, encoding=encoding))
    records.append(make_structural_row(0xCF, encoding=encoding))

    for program in normalized_programs[1:]:
        name = str(program["name"])
        comment = str(program.get("comment", name))
        records.extend(encode_comment(comment, 3, encoding))
        records.append(make_structural_row(0xD5, name, encoding))
        networks = program.get("networks", [])
        if not isinstance(networks, list):
            raise LadValidationError("subprogram.networks must be an array")
        for network in networks:
            if not isinstance(network, dict):
                raise LadValidationError("each network must be an object")
            if network.get("comment"):
                records.extend(encode_comment(str(network["comment"]), 1, encoding))
            records.extend(_compile_network_rows(network, encoding=encoding))
        records.append(make_structural_row(0xD0, name, encoding))

    return records


def compile_plan(plan: dict[str, Any]) -> LadDocument:
    if not isinstance(plan, dict):
        raise LadValidationError("plan must be a JSON object")
    schema = plan.get("schema", PLAN_SCHEMA)
    if schema != PLAN_SCHEMA:
        raise LadValidationError(f"unsupported plan schema: {schema!r}")
    encoding = str(plan.get("encoding", "gb2312"))
    # Exercise the selected codec before generating any bytes.
    try:
        "".encode(encoding)
    except LookupError as exc:
        raise LadValidationError(f"unknown encoding: {encoding}") from exc

    records = _build_records(plan, encoding)
    variables = plan.get("variables", [])
    if variables is None:
        variables = []
    if not isinstance(variables, list):
        raise LadValidationError("plan.variables must be an array")
    doc = LadDocument(records, encoding=encoding)
    doc.append_variable_comments(variables)
    return doc


def compile_networks(
    networks: list[dict[str, Any]],
    *,
    encoding: str = "gb2312",
) -> list[bytes]:
    """Compile network objects without adding a program or END wrapper."""

    if not isinstance(networks, list) or not networks:
        raise LadValidationError("networks must be a non-empty array")
    try:
        "".encode(encoding)
    except LookupError as exc:
        raise LadValidationError(f"unknown encoding: {encoding}") from exc
    records: list[bytes] = []
    for network in networks:
        if not isinstance(network, dict):
            raise LadValidationError("each network must be an object")
        if network.get("comment"):
            records.extend(encode_comment(str(network["comment"]), 1, encoding))
        records.extend(_compile_network_rows(network, encoding=encoding))
    return records


def build_plan(plan: dict[str, Any]) -> LadDocument:
    return compile_plan(plan)
