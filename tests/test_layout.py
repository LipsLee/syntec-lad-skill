from __future__ import annotations

import itertools
import re
import unittest
from collections import defaultdict, deque

from syntec_lad.compiler import compile_plan
from syntec_lad.inspection import inspect_document
from syntec_lad.native import find_lad_dll, validate_document


RD, RU, LD, LU = 0x02, 0x01, 0x08, 0x04


def canonical(device: str) -> str:
    text = device.upper()
    match = re.fullmatch(r"R(\d+)\.(\d+)", text)
    if match:
        return f"R{int(match.group(1)):04d}.{int(match.group(2)):02d}"
    match = re.fullmatch(r"([A-Z])(\d+)", text)
    if match:
        return f"{match.group(1)}{int(match.group(2)):03d}"
    return text


def devices_in(condition: dict) -> list[str]:
    if "contact" in condition:
        return [canonical(str(condition["contact"]))]
    result: list[str] = []
    for key in ("all", "any"):
        if key in condition:
            for child in condition[key]:
                result.extend(devices_in(child))
    return result


def evaluate(condition: dict, values: dict[str, bool]) -> bool:
    if "always" in condition:
        return bool(condition["always"])
    if "contact" in condition:
        state = values[canonical(str(condition["contact"]))]
        return not state if condition.get("normally") == "closed" else state
    if "all" in condition:
        return all(evaluate(child, values) for child in condition["all"])
    if "any" in condition:
        return any(evaluate(child, values) for child in condition["any"])
    raise ValueError(f"unsupported condition: {condition!r}")


def conducts(cell: dict, values: dict[str, bool]) -> bool:
    mnemonic = cell["mnemonic"]
    if mnemonic == "NOP":
        return False
    if mnemonic == "LINE":
        return True
    if mnemonic == "LD":
        return values.get(canonical(str(cell["value"])), True)
    if mnemonic == "LDI":
        return not values.get(canonical(str(cell["value"])), True)
    return True


def simulate(rows: list[dict], values: dict[str, bool], operand: str = "O000") -> bool:
    """Evaluate the ladder graph encoded in the rows.

    Each row boundary is a node.  A conducting cell joins its left and right
    boundaries; RD/RU and LD/LU flag pairs join the same boundary on adjacent
    rows.  The left rail feeds column zero on every row.
    """

    edges: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    flags: dict[tuple[int, int], dict[str, bool]] = defaultdict(dict)
    action: tuple[int, int] | None = None

    def connect(left: tuple[int, int], right: tuple[int, int]) -> None:
        edges[left].append(right)
        edges[right].append(left)

    for row_index, row in enumerate(rows):
        for cell in row["cells"]:
            column = cell["column"]
            width = cell["size"] // 4
            boundary = column + width
            connection = cell["connection"]
            if (
                cell["mnemonic"] == "OUT"
                and action is None
                and canonical(str(cell.get("value") or "")) == operand
            ):
                action = (row_index, column)
            if conducts(cell, values):
                connect((row_index, column), (row_index, boundary))
            if connection & RD:
                flags[(row_index, boundary)]["rd"] = True
            if connection & RU:
                flags[(row_index, boundary)]["ru"] = True
            if connection & LD:
                flags[(row_index, column)]["ld"] = True
            if connection & LU:
                flags[(row_index, column)]["lu"] = True

    for row_index in range(len(rows) - 1):
        for boundary in range(11):
            upper = flags.get((row_index, boundary), {})
            lower = flags.get((row_index + 1, boundary), {})
            if (upper.get("rd") and lower.get("ru")) or (
                upper.get("ld") and lower.get("lu")
            ):
                connect((row_index, boundary), (row_index + 1, boundary))

    source = (-1, 0)
    for row_index in range(len(rows)):
        connect(source, (row_index, 0))
    if action is None:
        raise AssertionError("compiled network has no OUT action")

    queue = deque([source])
    seen = {source}
    while queue:
        node = queue.popleft()
        for neighbor in edges[node]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return action in seen


def plan_for(condition: dict, *, layout: str | None = None) -> dict:
    plan = {
        "schema": "syntec-lad-plan/v1",
        "programs": [
            {
                "name": "MAIN",
                "networks": [
                    {
                        "condition": condition,
                        "actions": [{"op": "OUT", "operand": "O000"}],
                    }
                ],
            }
        ],
    }
    if layout is not None:
        plan["layout"] = layout
    return plan


def code_rows(document) -> list[dict]:
    report = inspect_document(document)
    return [
        row
        for row in report["rows"]
        if row["kind"] == "code" and row["cells"][0]["mnemonic"] != "END"
    ]


class LayoutSemanticsTests(unittest.TestCase):
    def assert_equivalent(self, condition: dict) -> None:
        document = compile_plan(plan_for(condition))
        rows = code_rows(document)
        devices = sorted(set(devices_in(condition)))
        for bits in itertools.product((False, True), repeat=len(devices)):
            values = dict(zip(devices, bits))
            with self.subTest(values=values):
                self.assertEqual(
                    simulate(rows, values),
                    evaluate(condition, values),
                )
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(document)

    def test_factored_prefix_and_suffix(self) -> None:
        self.assert_equivalent(
            {
                "all": [
                    {"contact": "I001"},
                    {"contact": "I002"},
                    {"any": [{"contact": "I003"}, {"contact": "I004"}]},
                    {"contact": "I005"},
                    {"contact": "I006"},
                ]
            }
        )

    def test_mixed_group_heights(self) -> None:
        self.assert_equivalent(
            {
                "all": [
                    {"contact": "I001"},
                    {
                        "any": [
                            {"contact": "I002"},
                            {"contact": "I003"},
                            {"contact": "I004"},
                        ]
                    },
                    {"contact": "I005"},
                    {"any": [{"contact": "I006"}, {"contact": "I007"}]},
                    {"contact": "I008"},
                ]
            }
        )

    def test_leading_groups(self) -> None:
        self.assert_equivalent(
            {
                "all": [
                    {"any": [{"contact": "I001"}, {"contact": "I002"}]},
                    {"any": [{"contact": "I003"}, {"contact": "I004"}]},
                    {"any": [{"contact": "I005"}, {"contact": "I006"}]},
                ]
            }
        )

    def test_non_factorable_dnf_falls_back(self) -> None:
        self.assert_equivalent(
            {
                "any": [
                    {"all": [{"contact": "I001"}, {"contact": "I002"}]},
                    {"all": [{"contact": "I003"}, {"contact": "I004"}]},
                ]
            }
        )

    def test_group_at_last_column(self) -> None:
        self.assert_equivalent(
            {
                "all": [{"contact": f"I{index:03d}"} for index in range(1, 9)]
                + [{"any": [{"contact": "I009"}, {"contact": "I010"}]}]
            }
        )

    def test_reported_tool_change_network(self) -> None:
        condition = {
            "all": [
                {"contact": "A040"},
                {"any": [{"contact": "A012"}, {"contact": "A013"}]},
                {"any": [{"contact": "S069"}, {"contact": "R075.00"}]},
                {"contact": "A031"},
                {"contact": "A051"},
                {"contact": "R044.05"},
            ]
        }
        document = compile_plan(plan_for(condition))
        rows = code_rows(document)
        self.assertEqual(len(rows), 2)
        top = [cell["value"] for cell in rows[0]["cells"] if cell["mnemonic"] == "LD"]
        bottom = [cell["value"] for cell in rows[1]["cells"] if cell["mnemonic"] == "LD"]
        self.assertEqual(top.count("A040"), 1)
        self.assertNotIn("A040", bottom)
        self.assertEqual(set(bottom), {"A013", "R0075.00"})
        self.assert_equivalent(condition)

    def test_parallel_actions_share_one_condition(self) -> None:
        condition = {"all": [{"contact": "I001"}, {"contact": "I002"}]}
        plan = plan_for(condition)
        plan["programs"][0]["networks"][0]["actions"] = [
            {"op": "OUT", "operand": "O000"},
            {"op": "OUT", "operand": "O001"},
        ]
        document = compile_plan(plan)
        rows = code_rows(document)
        self.assertEqual(len(rows), 2)

        def contacts(row: dict) -> list[str]:
            return [
                str(cell["value"])
                for cell in row["cells"]
                if cell["mnemonic"] in ("LD", "LDI")
            ]

        self.assertEqual(contacts(rows[0]), ["I001", "I002"])
        self.assertEqual(contacts(rows[1]), [])
        for bits in itertools.product((False, True), repeat=2):
            values = dict(zip(("I001", "I002"), bits))
            expected = evaluate(condition, values)
            self.assertEqual(simulate(rows, values, "O000"), expected)
            self.assertEqual(simulate(rows, values, "O001"), expected)
        try:
            find_lad_dll()
        except Exception:
            return
        validate_document(document)

    def test_parallel_actions_with_mixed_width_share_one_condition(self) -> None:
        condition = {"all": [{"contact": "I001"}, {"contact": "I002"}]}
        plan = plan_for(condition)
        plan["programs"][0]["networks"][0]["actions"] = [
            {"op": "OUT", "operand": "O000"},
            {"op": "TIM", "timer": "TM000", "preset": "#10"},
        ]
        document = compile_plan(plan)
        rows = code_rows(document)
        self.assertEqual(len(rows), 2)

        def contacts(row: dict) -> list[str]:
            return [
                str(cell["value"])
                for cell in row["cells"]
                if cell["mnemonic"] in ("LD", "LDI")
            ]

        self.assertEqual(contacts(rows[0]), ["I001", "I002"])
        self.assertEqual(contacts(rows[1]), [])
        self.assertTrue(
            any(
                cell["mnemonic"] == "OUT" and cell["column"] == 9
                for cell in rows[0]["cells"]
            )
        )
        self.assertTrue(
            any(
                cell["mnemonic"] == "TIM" and cell["column"] == 8
                for cell in rows[1]["cells"]
            )
        )
        for bits in itertools.product((False, True), repeat=2):
            values = dict(zip(("I001", "I002"), bits))
            self.assertEqual(
                simulate(rows, values, "O000"),
                evaluate(condition, values),
            )
        try:
            find_lad_dll()
        except Exception:
            return
        validate_document(document)

    def test_nine_contact_condition_drives_two_coils(self) -> None:
        condition = {"all": [{"contact": f"I{index:03d}"} for index in range(1, 10)]}
        plan = plan_for(condition)
        plan["programs"][0]["networks"][0]["actions"] = [
            {"op": "OUT", "operand": "O000"},
            {"op": "OUT", "operand": "O001"},
        ]
        document = compile_plan(plan)
        rows = code_rows(document)
        self.assertEqual(len(rows), 2)

        def contacts(row: dict) -> list[str]:
            return [
                str(cell["value"])
                for cell in row["cells"]
                if cell["mnemonic"] in ("LD", "LDI")
            ]

        self.assertEqual(len(contacts(rows[0])), 9)
        self.assertEqual(contacts(rows[1]), [])
        for bits in itertools.product((False, True), repeat=9):
            values = dict(zip((f"I{index:03d}" for index in range(1, 10)), bits))
            expected = evaluate(condition, values)
            self.assertEqual(simulate(rows, values, "O000"), expected)
            self.assertEqual(simulate(rows, values, "O001"), expected)

    def test_flow_action_does_not_share_a_parallel_bus(self) -> None:
        condition = {"contact": "I001"}
        plan = plan_for(condition)
        plan["programs"][0]["networks"][0]["actions"] = [
            {"op": "MSG", "target": "HELLO"},
            {"op": "OUT", "operand": "O000"},
        ]
        document = compile_plan(plan)
        rows = code_rows(document)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [
                cell["mnemonic"]
                for cell in rows[0]["cells"]
                if cell["mnemonic"] in ("LD", "LDI")
            ],
            ["LD"],
        )
        self.assertEqual(
            [
                cell["mnemonic"]
                for cell in rows[1]["cells"]
                if cell["mnemonic"] in ("LD", "LDI")
            ],
            ["LD"],
        )

    def test_nested_parallel_tool_change_network(self) -> None:
        condition = {
            "all": [
                {
                    "any": [
                        {
                            "all": [
                                {"contact": "A040"},
                                {
                                    "any": [
                                        {"contact": "A012"},
                                        {"contact": "A013"},
                                    ]
                                },
                                {
                                    "any": [
                                        {"contact": "S069"},
                                        {"contact": "R0075.00"},
                                    ]
                                },
                                {"contact": "A031", "normally": "closed"},
                            ]
                        },
                        {
                            "all": [
                                {"contact": "A043"},
                                {"contact": "A044", "normally": "closed"},
                            ]
                        },
                    ]
                },
                {"contact": "A051"},
                {"contact": "R0044.05", "normally": "closed"},
            ]
        }
        document = compile_plan(plan_for(condition))
        rows = code_rows(document)
        self.assertEqual(len(rows), 3)

        def contacts(row: dict) -> list[str]:
            return [
                str(cell["value"])
                for cell in row["cells"]
                if cell["mnemonic"] in ("LD", "LDI")
            ]

        self.assertEqual(contacts(rows[0]).count("A040"), 1)
        self.assertNotIn("A040", contacts(rows[1]))
        self.assertEqual(contacts(rows[1]), ["A013", "R0075.00"])
        self.assertEqual(contacts(rows[2]), ["A043", "A044"])
        self.assert_equivalent(condition)

    def test_nested_parallel_expanded_opt_out(self) -> None:
        condition = {
            "all": [
                {
                    "any": [
                        {
                            "all": [
                                {"contact": "A040"},
                                {
                                    "any": [
                                        {"contact": "A012"},
                                        {"contact": "A013"},
                                    ]
                                },
                                {
                                    "any": [
                                        {"contact": "S069"},
                                        {"contact": "R0075.00"},
                                    ]
                                },
                                {"contact": "A031", "normally": "closed"},
                            ]
                        },
                        {
                            "all": [
                                {"contact": "A043"},
                                {"contact": "A044", "normally": "closed"},
                            ]
                        },
                    ]
                },
                {"contact": "A051"},
                {"contact": "R0044.05", "normally": "closed"},
            ]
        }
        document = compile_plan(plan_for(condition, layout="expanded"))
        self.assertEqual(len(code_rows(document)), 5)
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(document)

    def test_expanded_layout_opt_out(self) -> None:
        condition = {
            "all": [
                {"contact": "A040"},
                {"any": [{"contact": "A012"}, {"contact": "A013"}]},
                {"any": [{"contact": "S069"}, {"contact": "R075.00"}]},
                {"contact": "A031"},
            ]
        }
        document = compile_plan(plan_for(condition, layout="expanded"))
        self.assertEqual(len(code_rows(document)), 4)
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(document)


if __name__ == "__main__":
    unittest.main()
