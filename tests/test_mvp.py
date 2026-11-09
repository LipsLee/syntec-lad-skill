from __future__ import annotations

import unittest

from syntec_lad.compiler import compile_plan
from syntec_lad.errors import LadUnsupportedError, LadValidationError
from syntec_lad.inspection import inspect_document, rows_connected
from syntec_lad.native import find_lad_dll, validate_document
from syntec_lad.opcodes import CONNECTION_LD, CONNECTION_RD


def network(
    actions: list[dict],
    condition: dict | None = None,
    comment: str | None = None,
) -> dict:
    result: dict = {"actions": actions}
    if condition is not None:
        result["condition"] = condition
    if comment is not None:
        result["comment"] = comment
    return result


def single_plan(
    condition: dict | None,
    actions: list[dict],
    *,
    encoding: str = "gb2312",
) -> dict:
    return {
        "schema": "syntec-lad-plan/v1",
        "encoding": encoding,
        "programs": [
            {
                "name": "MAIN",
                "networks": [network(actions, condition)],
            }
        ],
    }


def all_mvp_plan() -> dict:
    networks: list[dict] = []

    for op, operand in (
        ("OUT", "O000"),
        ("OUTI", "O001"),
        ("PLS", "O002"),
        ("PLF", "O003"),
        ("SET", "O004"),
        ("RST", "O005"),
    ):
        networks.append(network([{"op": op, "operand": operand}], {"contact": "I000"}))

    for device, normally in (
        ("I001", "open"),
        ("I002", "closed"),
        ("TM001", "open"),
        ("TM001", "closed"),
        ("CT001", "open"),
        ("CT001", "closed"),
    ):
        networks.append(
            network(
                [{"op": "OUT", "operand": "O006"}],
                {"contact": device, "normally": normally},
            )
        )

    for unit, preset in (
        ("1ms", "#1"),
        ("10ms", "#2"),
        ("100ms", "#3"),
        ("1s", "#4"),
    ):
        networks.append(
            network(
                [{"op": "TIM", "timer": "TM000", "unit": unit, "preset": preset}],
                {"contact": "I003"},
            )
        )
    for unit, preset in (
        ("1ms", "@R10"),
        ("10ms", "@R11"),
        ("100ms", "@R12"),
        ("1s", "@R13"),
    ):
        networks.append(
            network(
                [{"op": "TIM", "timer": "TM001", "unit": unit, "preset": preset}],
                {"contact": "I004"},
            )
        )

    for op in ("CNTUP", "CNTDOWN", "CNTRINGUP", "CNTRINGDOWN"):
        networks.append(
            network([{"op": op, "counter": "CT000", "preset": "#5"}], {"contact": "I005"})
        )
    for op in ("CNTUP", "CNTDOWN", "CNTRINGUP", "CNTRINGDOWN"):
        networks.append(
            network([{"op": op, "counter": "CT001", "preset": "@R14"}], {"contact": "I006"})
        )
    networks.append(network([{"op": "CNTRESET", "counter": "CT000"}], {"contact": "I007"}))

    networks.append(network([{"op": "JSR", "target": "SUB1"}], {"always": True}))
    networks.append(network([{"op": "JMP", "target": "SUB1"}], {"contact": "I010"}))
    networks.append(network([{"op": "MSG", "target": "ALM001"}], {"contact": "I011"}))

    for op in ("MOV", "ADD", "SUB", "MUL", "DIV", "AND", "OR", "XOR"):
        networks.append(
            network([{"op": op, "src": "#10", "dst": "R0001"}], {"always": True})
        )
    for op in ("MOV", "ADD", "SUB", "MUL", "DIV", "AND", "OR", "XOR"):
        networks.append(
            network([{"op": op, "src": "@R0002", "dst": "@R0003"}], {"always": True})
        )

    for relation in (">", "<", "="):
        networks.append(
            network(
                [{"op": "OUT", "operand": "O007"}],
                {"compare": {"relation": relation, "value": "R0001", "other": "#5"}},
            )
        )
        networks.append(
            network(
                [{"op": "OUT", "operand": "O008"}],
                {"compare": {"relation": relation, "value": "@R0001", "other": "@R0002"}},
            )
        )

    return {
        "schema": "syntec-lad-plan/v1",
        "encoding": "gb2312",
        "programs": [
            {
                "name": "MAIN",
                "comment": "MVP opcode coverage",
                "networks": networks,
            },
            {
                "name": "SUB1",
                "comment": "MVP subprogram",
                "networks": [
                    network(
                        [{"op": "OUT", "operand": "O020"}],
                        {"contact": "I030"},
                    )
                ],
            },
        ],
        "variables": [{"name": "R0001", "short": "temp", "long": "test"}],
    }


class MvpCompilerTests(unittest.TestCase):
    def _native(self, document) -> None:
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(document)

    def test_all_mvp_opcodes_and_operand_variants(self) -> None:
        document = compile_plan(all_mvp_plan())
        report = inspect_document(document)
        self.assertEqual(report["integrity"]["issues"], [])
        self.assertEqual(report["version"], 2)

        cells = [
            cell
            for row in report["rows"]
            if row["kind"] == "code"
            for cell in row["cells"]
            if cell["opcode"] not in (0x00, 0x0D)
        ]
        mnemonics = {cell["mnemonic"] for cell in cells}
        self.assertTrue(
            {
                "LD",
                "LDI",
                "LDT",
                "LDTI",
                "LDC",
                "LDCI",
                "OUT",
                "OUTI",
                "PLS",
                "PLF",
                "SET",
                "RST",
                "TIM",
                "CNTUP",
                "CNTDOWN",
                "CNTRESET",
                "CNTRINGUP",
                "CNTRINGDOWN",
                "JMP",
                "JSR",
                "MSG",
                "LAB",
                "RTS",
                "END",
                "MOV",
                "ADD",
                "SUB",
                "MUL",
                "DIV",
                "AND",
                "OR",
                "XOR",
                "CMPG",
                "CMPL",
                "CMPE",
            }.issubset(mnemonics)
        )

        opcodes = {cell["opcode"] for cell in cells}
        self.assertTrue(
            {0xC2, 0xC3, 0xC4, 0xC5, 0xE2, 0xE3, 0xE4, 0xE7}.issubset(opcodes)
        )
        self.assertTrue(
            {0xC6, 0xC7, 0xC9, 0xE0, 0xE1, 0xE5, 0xE6, 0xE9, 0xEA}.issubset(opcodes)
        )
        self.assertTrue(
            {
                0x21,
                0x27,
                0x33,
                0x39,
                0x45,
                0x4B,
                0x57,
                0x5D,
                0x70,
                0x72,
                0x81,
                0x87,
                0x93,
                0x99,
                0xA5,
                0xAB,
            }.issubset(opcodes)
        )
        self.assertTrue({0x5E, 0x62, 0x66, 0x6A, 0x6C, 0x6E}.issubset(opcodes))
        self._native(document)

    def test_layouts_pass_native_syntax_check(self) -> None:
        conditions = [
            {"all": [{"contact": f"I{i:03d}"} for i in range(8)]},
            {
                "all": [{"contact": f"I{i:03d}"} for i in range(7)]
                + [{"compare": {"relation": ">", "value": "R1", "other": "#1"}}]
            },
            {"any": [{"contact": "I001"}, {"contact": "I002"}, {"contact": "I003"}]},
            {
                "any": [
                    {"all": [{"contact": f"I{i:03d}"} for i in range(8)]},
                    {"all": [{"contact": f"I{i + 10:03d}"} for i in range(8)]},
                ]
            },
            {
                "all": [
                    {"contact": "I001"},
                    {"any": [{"contact": "I002"}, {"all": [{"contact": "I003"}, {"contact": "I004"}]}]},
                ]
            },
            {
                "any": [
                    {"compare": {"relation": "<", "value": "R1", "other": "#2"}},
                    {"all": [{"contact": "I001"}, {"contact": "I002"}]},
                ]
            },
        ]
        plan = {
            "schema": "syntec-lad-plan/v1",
            "programs": [
                {
                    "name": "MAIN",
                    "networks": [
                        network([{"op": "OUT", "operand": "O000"}], condition)
                        for condition in conditions
                    ],
                }
            ],
        }
        document = compile_plan(plan)
        report = inspect_document(document)
        self.assertEqual(report["integrity"]["issues"], [])
        self.assertEqual(len(report["network_candidates"]), len(conditions))
        self._native(document)

    def test_layout_and_flow_negative_cases(self) -> None:
        too_wide = {
            "all": [{"contact": f"I{i:03d}"} for i in range(8)]
            + [{"compare": {"relation": ">", "value": "R1", "other": "#1"}}]
        }
        with self.assertRaises(LadUnsupportedError):
            compile_plan(single_plan(too_wide, [{"op": "OUT", "operand": "O000"}]))

        with self.assertRaises(LadUnsupportedError):
            compile_plan(
                single_plan(
                    {
                        "any": [
                            {"all": [{"contact": f"I{i:03d}"} for i in range(9)]},
                            {"contact": "I020"},
                        ]
                    },
                    [{"op": "OUT", "operand": "O000"}],
                )
            )

        with self.assertRaises(LadValidationError):
            compile_plan(
                single_plan(
                    {"contact": "I000"},
                    [{"op": "JSR", "target": "MAIN"}],
                )
            )
        with self.assertRaises(LadValidationError):
            compile_plan(
                single_plan(
                    {"contact": "I000"},
                    [{"op": "CNTRESET", "counter": "CT000", "preset": "#1"}],
                )
            )
        with self.assertRaises(LadValidationError):
            compile_plan(
                single_plan(
                    {"all": [{"contact": "I000"}], "always": True},
                    [{"op": "OUT", "operand": "O000"}],
                )
            )
        with self.assertRaises(LadValidationError):
            compile_plan(
                single_plan(
                    {"contact": "I000", "bogus": True},
                    [{"op": "OUT", "operand": "O000"}],
                )
            )
        with self.assertRaises(LadValidationError):
            compile_plan(
                single_plan(
                    {"always": True},
                    [{"op": "MSG", "target": "TOO_LONG"}],
                )
            )

    def test_comment_encoding_and_missing_device_values(self) -> None:
        with self.assertRaises(LadValidationError):
            compile_plan(
                {
                    "schema": "syntec-lad-plan/v1",
                    "encoding": "gb2312",
                    "programs": [
                        {
                            "name": "MAIN",
                            "comment": "\U0001F600",
                            "networks": [
                                network(
                                    [{"op": "OUT", "operand": "O000"}],
                                    {"contact": "I000"},
                                )
                            ],
                        }
                    ],
                }
            )
        with self.assertRaises(LadValidationError):
            compile_plan(
                single_plan(
                    {"contact": "I000"},
                    [{"op": "MOV", "src": "#1"}],
                )
            )
        with self.assertRaises(LadValidationError):
            compile_plan(
                single_plan(
                    {"contact": "I000"},
                    [{"op": "TIM", "preset": "#1"}],
                )
            )

    def test_rows_connected_requires_matching_vertical_bits(self) -> None:
        upper = bytearray(40)
        upper[0] = CONNECTION_RD
        upper[1] = 0x01
        lower = bytearray(40)
        lower[1] = 0x01
        self.assertFalse(rows_connected(bytes(upper), bytes(lower)))

        lower[0] = 0x01
        self.assertTrue(rows_connected(bytes(upper), bytes(lower)))

        upper[0] = CONNECTION_LD
        lower[0] = 0x04
        self.assertTrue(rows_connected(bytes(upper), bytes(lower)))


if __name__ == "__main__":
    unittest.main()
