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


@dataclass(frozen=True)
class ConditionNode:
    """A condition tree that keeps ``all``/``any`` nesting for layout."""

    kind: str
    leaf: Leaf | None = None
    children: tuple["ConditionNode", ...] = ()


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


def _parse_condition_node(condition: Any) -> ConditionNode:
    if condition is None:
        return ConditionNode("true")
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
        children = tuple(
            node
            for node in (_parse_condition_node(value) for value in values)
            if node.kind != "true"
        )
        if not children:
            return ConditionNode("true")
        return ConditionNode("all", children=children)
    if "any" in condition:
        values = condition["any"]
        if not isinstance(values, list) or not values:
            raise LadValidationError("condition.any must be a non-empty array")
        children = tuple(_parse_condition_node(value) for value in values)
        if any(node.kind == "true" for node in children):
            return ConditionNode("true")
        return ConditionNode("any", children=children)
    if "contact" in condition:
        device = str(condition["contact"])
        normally = str(condition.get("normally", "open")).lower()
        if normally not in ("open", "closed"):
            raise LadValidationError(
                f"contact.normally must be 'open' or 'closed': {normally!r}"
            )
        contact_kind, operand = parse_contact_device(device)
        opcode = CONTACT_OPCODES[(contact_kind, normally)]
        return ConditionNode(
            "leaf",
            leaf=Leaf("contact", opcode, 1, operand, None, source=device.upper()),
        )
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
        return ConditionNode(
            "leaf",
            leaf=Leaf("compare", raw_opcode, 2, other, value, source=relation),
        )
    if "always" in condition:
        if not condition["always"]:
            raise LadValidationError("condition.always must be true when used")
        return ConditionNode("true")
    raise LadValidationError(
        "condition must contain all, any, contact, compare, or always"
    )


def _condition_dnf(node: ConditionNode) -> list[list[Leaf]]:
    if node.kind == "true":
        return [[]]
    if node.kind == "leaf":
        if node.leaf is None:
            raise LadValidationError("leaf condition is missing its contact")
        return [[node.leaf]]
    if node.kind == "all":
        result: list[list[Leaf]] = [[]]
        for child in node.children:
            child_terms = _condition_dnf(child)
            result = [left + right for left in result for right in child_terms]
            result = _limit_dnf(result)
        return result
    if node.kind == "any":
        result: list[list[Leaf]] = []
        for child in node.children:
            result.extend(_condition_dnf(child))
        if any(not term for term in result):
            return [[]]
        return _limit_dnf(result)
    raise LadValidationError(f"unknown condition node: {node.kind}")


def _normalize_condition(condition: Any) -> list[list[Leaf]]:
    return _condition_dnf(_parse_condition_node(condition))


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


def _common_suffix(terms: list[list[Leaf]], limit: int) -> int:
    if not terms or limit <= 0:
        return 0
    length = 0
    while length < limit:
        key = terms[0][-1 - length].key
        if any(term[-1 - length].key != key for term in terms[1:]):
            break
        length += 1
    return length


def _factor_terms(terms: list[list[Leaf]]) -> list[list[list[Leaf]]] | None:
    """Factor a DNF into series elements, each with parallel alternatives.

    The result is only produced when the DNF is exactly the Cartesian product
    of the choices at every position.  This covers the common case produced by
    ``all`` containing several independent ``any`` groups, e.g.

        A and (B or C) and D and (E or F)

    which becomes ``[[A], [B, C], [D], [E, F]]`` instead of four expanded rows
    that repeat A and D.  When the product check fails, ``None`` is returned
    and the caller keeps the deterministic expanded layout.
    """

    unique: list[list[Leaf]] = []
    seen: set[tuple[object, ...]] = set()
    for term in terms:
        key = tuple(leaf.key for leaf in term)
        if key in seen:
            continue
        seen.add(key)
        unique.append(term)
    if not unique or any(not term for term in unique):
        return None
    if len(unique) == 1:
        return [[[leaf]] for leaf in unique[0]]

    prefix_len = _common_prefix(unique)
    min_len = min(len(term) for term in unique)
    suffix_len = _common_suffix(unique, min_len - prefix_len)
    middles = [
        term[prefix_len:len(term) - suffix_len if suffix_len else len(term)]
        for term in unique
    ]
    if any(not middle for middle in middles):
        return None
    width = len(middles[0])
    if any(len(middle) != width for middle in middles):
        return None

    columns: list[list[Leaf]] = []
    for index in range(width):
        alternatives: list[Leaf] = []
        keys: set[tuple[object, ...]] = set()
        for middle in middles:
            leaf = middle[index]
            if leaf.key in keys:
                continue
            keys.add(leaf.key)
            alternatives.append(leaf)
        columns.append(alternatives)

    product = 1
    for alternatives in columns:
        product *= len(alternatives)
        if product > 64:
            return None
    if product != len(middles):
        return None

    elements: list[list[list[Leaf]]] = []
    for leaf in unique[0][:prefix_len]:
        elements.append([[leaf]])
    for alternatives in columns:
        elements.append([[leaf] for leaf in alternatives])
    if suffix_len:
        for leaf in unique[0][-suffix_len:]:
            elements.append([[leaf]])
    return elements


@dataclass
class _ConditionLayout:
    height: int
    width: int
    leaves: list[tuple[int, int, Leaf]]
    lines: list[tuple[int, int]]
    buses: list[tuple[int, int, int]]


def _layout_condition_node(node: ConditionNode) -> _ConditionLayout:
    """Lay out the condition tree directly, preserving nested all/any blocks.

    Every ``any`` node gets a vertical bus at its left and right edges.  An
    ``all`` node simply places its children left to right and top aligned; the
    buses owned by a child ``any`` merge that child's rows at exactly the
    column where the next series element begins.
    """

    if node.kind == "true":
        return _ConditionLayout(1, 0, [], [], [])
    if node.kind == "leaf":
        if node.leaf is None:
            raise LadValidationError("leaf condition is missing its contact")
        return _ConditionLayout(1, node.leaf.width, [(0, 0, node.leaf)], [], [])
    if node.kind == "all":
        if not node.children:
            return _ConditionLayout(1, 0, [], [], [])
        children = [_layout_condition_node(child) for child in node.children]
        height = max(child.height for child in children)
        width = sum(child.width for child in children)
        leaves: list[tuple[int, int, Leaf]] = []
        lines: list[tuple[int, int]] = []
        buses: list[tuple[int, int, int]] = []
        column = 0
        for child in children:
            leaves.extend(
                (row, column + child_column, leaf)
                for row, child_column, leaf in child.leaves
            )
            lines.extend(
                (row, column + child_column)
                for row, child_column in child.lines
            )
            buses.extend(
                (child_column + column, top, bottom)
                for child_column, top, bottom in child.buses
            )
            column += child.width
        return _ConditionLayout(height, width, leaves, lines, buses)
    if node.kind == "any":
        if not node.children:
            raise LadValidationError("condition.any must be a non-empty array")
        children = [_layout_condition_node(child) for child in node.children]
        width = max(child.width for child in children)
        height = sum(child.height for child in children)
        leaves: list[tuple[int, int, Leaf]] = []
        lines: list[tuple[int, int]] = []
        buses: list[tuple[int, int, int]] = []
        row_offset = 0
        for child in children:
            leaves.extend(
                (row + row_offset, child_column, leaf)
                for row, child_column, leaf in child.leaves
            )
            lines.extend(
                (row + row_offset, child_column)
                for row, child_column in child.lines
            )
            # A branch only needs to reach the common right bus on its first
            # row.  Lower rows either end at an inner merge bus (already
            # connected to the first row) or are pass-through rows that must
            # not be given a conducting path.
            lines.extend(
                (row_offset, child_column)
                for child_column in range(child.width, width)
            )
            buses.extend(
                (child_column, top + row_offset, bottom + row_offset)
                for child_column, top, bottom in child.buses
            )
            row_offset += child.height
        if height > 1:
            buses.append((0, 0, height - 1))
            buses.append((width, 0, height - 1))
        return _ConditionLayout(height, width, leaves, lines, buses)
    raise LadValidationError(f"unknown condition node: {node.kind}")


def _apply_bus(
    layouts: list[RowLayout],
    column: int,
    top_row: int,
    bottom_row: int,
) -> None:
    """Draw one vertical bus, anchoring both sides of every row it crosses."""

    if top_row >= bottom_row:
        return
    if column <= 0:
        # Column zero is the power rail; parallel branches already meet there.
        return
    if column >= 10:
        raise LadUnsupportedError(f"cannot place a bus at column {column}")
    if top_row < 0 or bottom_row >= len(layouts):
        raise LadUnsupportedError("bus extends outside the network rows")
    for row in range(top_row, bottom_row + 1):
        layout = layouts[row]
        left = layout.unit_ending_at(column)
        if left is None:
            if layout.occupied[column - 1]:
                raise LadUnsupportedError(
                    f"cannot place the bus at column {column} on row {row}"
                )
            left = layout.place(column - 1, make_nop_cell(), "NOP")
        left.right_flags(
            0x02 if row == top_row else 0x01 if row == bottom_row else 0x03
        )

        right = layout.unit_starting_at(column)
        if right is None:
            if layout.occupied[column]:
                raise LadUnsupportedError(
                    f"cannot place the bus at column {column} on row {row}"
                )
            right = layout.place(column, make_nop_cell(), "NOP")
        right.left_flags(
            0x08 if row == top_row else 0x04 if row == bottom_row else 0x0C
        )


def _compile_tree_rows(
    node: ConditionNode,
    actions: list[Action],
    *,
    encoding: str,
) -> list[bytes]:
    """Lay out a condition tree with one bus per ``any`` edge.

    Several actions driven by the same condition are stacked behind one
    vertical bus.  One-cell coils sit at column 9 with a LINE at column 8;
    two-cell timers/counters/flow actions start at column 8.  A mixed group
    therefore places the bus at column 8 and gives each coil its own LINE,
    which is the same leading cell a normal coil rung already uses.
    """

    if not actions:
        raise LadValidationError("network requires at least one action")
    layout = _layout_condition_node(node)
    action_start = min(action.action_slot for action in actions)
    merged = len(actions) > 1
    if merged and any(action.op in FLOW_OPCODES for action in actions):
        raise LadUnsupportedError(
            "flow actions cannot share a parallel action bus"
        )
    for action in actions:
        if action.action_slot == action_start:
            continue
        if action.action_slot == action_start + 1 and action.width == 1:
            continue
        else:
            raise LadUnsupportedError(
                "parallel actions must share one action bus"
            )
    if layout.width > action_start:
        raise LadUnsupportedError(
            f"network needs {layout.width} columns but action starts at column {action_start}"
        )
    if layout.height > 64:
        raise LadUnsupportedError("condition layout needs more than 64 rows")

    layouts = [RowLayout() for _ in range(max(layout.height, len(actions)))]
    for row, column, leaf in layout.leaves:
        layouts[row].place(column, _make_leaf_unit(leaf, encoding), leaf.kind)
    for row, column in layout.lines:
        layouts[row].place(column, make_line_cell(), "LINE")

    top = layouts[0]
    top.fill_lines(layout.width, action_start - 1)
    for index, action in enumerate(actions):
        unit = _make_action_unit(action, encoding)
        if action.action_slot == action_start:
            layouts[index].place(action_start, unit, "ACTION")
        else:
            layouts[index].place(action_start, make_line_cell(), "LINE")
            layouts[index].place(action_start + 1, unit, "ACTION")

    if merged:
        anchor = top.unit_ending_at(action_start)
        if anchor is None:
            raise LadUnsupportedError("not enough room for the parallel-action bus")
        _apply_bus(layouts, action_start, 0, len(actions) - 1)

    for column, top_row, bottom_row in layout.buses:
        _apply_bus(layouts, column, top_row, bottom_row)

    return [row.render() for row in layouts]


def _compile_factored_rows(
    elements: list[list[list[Leaf]]],
    action: Action,
    *,
    encoding: str,
) -> list[bytes]:
    """Lay out a series of parallel groups, sharing each group's common wire.

    Every parallel boundary is encoded on both sides: the cells ending at the
    boundary carry RD/RU flags and the cells starting there carry LD/LU flags.
    Missing cells on the lower rows are filled with NOP placeholders.  This is
    the pattern used by MLCEdit itself for multi-row rungs.
    """

    layouts: list[RowLayout] = []

    def row(index: int) -> RowLayout:
        while len(layouts) <= index:
            layouts.append(RowLayout())
        return layouts[index]

    action_start = action.action_slot
    column = 0
    buses: dict[int, int] = {}
    for alternatives in elements:
        if not alternatives:
            raise LadUnsupportedError("a series element needs at least one branch")
        widths = [sum(leaf.width for leaf in branch) for branch in alternatives]
        group_width = max(widths)
        height = len(alternatives)
        start = column
        end = start + group_width
        if end > action_start:
            raise LadUnsupportedError(
                f"network needs {end} columns but action starts at column {action_start}"
            )
        for index, branch in enumerate(alternatives):
            layout = row(index)
            slot = start
            for leaf in branch:
                layout.place(slot, _make_leaf_unit(leaf, encoding), leaf.kind)
                slot += leaf.width
            # A short branch is padded with LINE so it reaches the merge bus.
            if slot < end:
                layout.fill_lines(slot, end - 1)
        if height > 1:
            if start > 0:
                buses[start] = max(buses.get(start, 0), height)
            buses[end] = max(buses.get(end, 0), height)
        column = end

    top = row(0)
    top.fill_lines(column, action_start - 1)
    top.place(action_start, _make_action_unit(action, encoding), "ACTION")

    for boundary, height in sorted(buses.items()):
        left_units: list[UnitPlacement] = []
        right_units: list[UnitPlacement] = []
        for index in range(height):
            layout = row(index)
            unit = layout.unit_ending_at(boundary)
            if unit is None:
                if boundary <= 0:
                    raise LadUnsupportedError(
                        "parallel group at column zero needs no left bus"
                    )
                if layout.unit_starting_at(boundary - 1) is not None:
                    raise LadUnsupportedError(
                        f"cannot place the left bus at column {boundary} on row {index}"
                    )
                unit = layout.place(boundary - 1, make_nop_cell(), "NOP")
            left_units.append(unit)

            unit = layout.unit_starting_at(boundary)
            if unit is None:
                if boundary >= 10:
                    raise LadUnsupportedError(
                        f"cannot place the right bus at column {boundary}"
                    )
                unit = layout.place(boundary, make_nop_cell(), "NOP")
            right_units.append(unit)

        last = height - 1
        for index, unit in enumerate(left_units):
            if index == 0:
                flags = 0x02
            elif index == last:
                flags = 0x01
            else:
                flags = 0x03
            unit.right_flags(flags)
        for index, unit in enumerate(right_units):
            if index == 0:
                flags = 0x08
            elif index == last:
                flags = 0x04
            else:
                flags = 0x0C
            unit.left_flags(flags)

    return [layout.render() for layout in layouts]


def _compile_expanded_rows(
    terms: list[list[Leaf]],
    action: Action,
    *,
    encoding: str,
) -> list[bytes]:
    """Lay out every DNF term as its own row (the pre-0.3.2 behavior)."""

    action_start = action.action_slot
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
        return [layout.render()]

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
            left.right_flags(left_flags)
            right.left_flags(right_flags)

    layouts[0].place(action_start, _make_action_unit(action, encoding), "ACTION")
    return [layout.render() for layout in layouts]


def _compile_network_rows(
    network: dict[str, Any],
    *,
    encoding: str,
    layout: str = "auto",
) -> list[bytes]:
    condition = _parse_condition_node(network.get("condition"))
    terms = _condition_dnf(condition)
    actions_raw = network.get("actions")
    if not isinstance(actions_raw, list) or not actions_raw:
        raise LadValidationError("each network requires at least one action")
    actions = [_parse_action(value) for value in actions_raw]
    layout_mode = str(network.get("layout", layout)).lower()
    if layout_mode not in ("auto", "expanded"):
        raise LadValidationError("layout must be 'auto' or 'expanded'")

    # A network in the plan means one conditional network.  Actions driven by
    # the same condition are laid out as parallel outputs behind a single bus,
    # including mixed 1-cell coils and 2-cell timers/counters.  Flow actions
    # and conditions too wide for the shared bus fall back to one row per
    # action.
    #
    # ``auto`` first lays out the original all/any tree.  This keeps nested
    # parallel blocks compact even when their DNF is not a Cartesian product.
    # If the tree cannot fit, it falls back to Cartesian-product factoring and
    # finally to the one-row-per-DNF-term layout.  ``expanded`` always uses the
    # last form.
    if layout_mode == "auto":
        try:
            return _compile_tree_rows(condition, actions, encoding=encoding)
        except LadUnsupportedError:
            pass

    all_rows: list[bytes] = []
    for action in actions:
        if action.width == 2 and action.action_slot != 8:
            raise LadUnsupportedError("two-cell actions must start at column 8")
        if layout_mode == "auto":
            try:
                all_rows.extend(
                    _compile_tree_rows(condition, [action], encoding=encoding)
                )
                continue
            except LadUnsupportedError:
                pass
            elements = _factor_terms(terms)
        else:
            elements = None
        if elements is not None:
            try:
                all_rows.extend(
                    _compile_factored_rows(elements, action, encoding=encoding)
                )
                continue
            except LadUnsupportedError:
                pass
        all_rows.extend(_compile_expanded_rows(terms, action, encoding=encoding))

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
    layout = str(plan.get("layout", "auto")).lower()
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
        records.extend(_compile_network_rows(network, encoding=encoding, layout=layout))
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
            records.extend(
                _compile_network_rows(network, encoding=encoding, layout=layout)
            )
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
    layout: str = "auto",
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
        records.extend(_compile_network_rows(network, encoding=encoding, layout=layout))
    return records


def build_plan(plan: dict[str, Any]) -> LadDocument:
    return compile_plan(plan)
