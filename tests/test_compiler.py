from __future__ import annotations

import os
import unittest
from pathlib import Path

from syntec_lad.compiler import compile_plan
from syntec_lad.errors import LadUnsupportedError, LadValidationError
from syntec_lad.inspection import inspect_document
from syntec_lad.native import find_lad_dll, validate_document


SAMPLE = Path(
    os.environ.get(
        "SYNTEC_SAMPLE_LAD",
        r"D:\Toolsyntec\pakeageTool\快速打包工具\DiskC\OpenCNC\Ladder\cnc.lad",
    )
)


def plan_with(condition: dict, actions: list[dict]) -> dict:
    return {
        "schema": "syntec-lad-plan/v1",
        "programs": [
            {
                "name": "MAIN",
                "networks": [{"condition": condition, "actions": actions}],
            }
        ],
    }


class CompilerTests(unittest.TestCase):
    def _native(self, plan: dict) -> None:
        document = compile_plan(plan)
        self.assertEqual(inspect_document(document)["integrity"]["issues"], [])
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(document)

    def test_series_and_parallel(self) -> None:
        self._native(plan_with({"all": [{"contact": "I000"}, {"contact": "I001"}]}, [{"op": "OUT", "operand": "O010"}]))
        self._native(plan_with({"any": [{"contact": "I000"}, {"contact": "I001"}]}, [{"op": "OUT", "operand": "O011"}]))
        self._native(
            plan_with(
                {
                    "all": [
                        {"contact": "I002"},
                        {"any": [{"contact": "I003"}, {"contact": "O011"}]},
                    ]
                },
                [{"op": "OUT", "operand": "O011"}],
            )
        )

    def test_timer_counter_compare_move_and_subprogram(self) -> None:
        plan = {
            "schema": "syntec-lad-plan/v1",
            "encoding": "gb2312",
            "programs": [
                {
                    "name": "MAIN",
                    "comment": "主程序",
                    "networks": [
                        {
                            "comment": "定时",
                            "condition": {"contact": "I006"},
                            "actions": [
                                {
                                    "op": "TIM",
                                    "timer": "TM000",
                                    "unit": "100ms",
                                    "preset": "#00010",
                                }
                            ],
                        },
                        {
                            "comment": "计数",
                            "condition": {"contact": "I007"},
                            "actions": [
                                {
                                    "op": "CNTUP",
                                    "counter": "CT000",
                                    "preset": "#00005",
                                }
                            ],
                        },
                        {
                            "comment": "比较",
                            "condition": {
                                "compare": {
                                    "relation": "=",
                                    "value": "R0001",
                                    "other": "#00005",
                                }
                            },
                            "actions": [{"op": "OUT", "operand": "O012"}],
                        },
                        {
                            "comment": "传送",
                            "condition": {"always": True},
                            "actions": [{"op": "MOV", "src": "#00010", "dst": "R0001"}],
                        },
                        {
                            "comment": "调用",
                            "condition": {"always": True},
                            "actions": [{"op": "JSR", "target": "SUB1"}],
                        },
                    ],
                },
                {
                    "name": "SUB1",
                    "comment": "子程序",
                    "networks": [
                        {
                            "condition": {"contact": "I020"},
                            "actions": [{"op": "OUT", "operand": "O020"}],
                        }
                    ],
                },
            ],
        }
        document = compile_plan(plan)
        result = inspect_document(document)
        self.assertEqual(len(result["programs"]), 2)
        self.assertEqual(result["programs"][1]["name"], "SUB1")
        compare_cells = [
            cell
            for row in result["rows"]
            for cell in row["cells"]
            if cell["mnemonic"] == "CMPE"
        ]
        self.assertEqual(len(compare_cells), 1)
        self.assertEqual(compare_cells[0]["value"], "#00005 R0001")
        self._native(plan)

    def test_invalid_subprogram_and_missing_values(self) -> None:
        with self.assertRaises(LadValidationError):
            compile_plan(
                {
                    "schema": "syntec-lad-plan/v1",
                    "programs": [
                        {
                            "name": "MAIN",
                            "networks": [
                                {
                                    "condition": {"always": True},
                                    "actions": [{"op": "MOV", "src": "#1", "dst": "R1"}],
                                }
                            ],
                        },
                        {
                            "name": "TOO_LONG",
                            "networks": [],
                        },
                    ],
                }
            )
        with self.assertRaises(LadValidationError):
            compile_plan(plan_with({"contact": "I000"}, [{"op": "TIM", "timer": "TM000"}]))

    def test_too_wide_layout(self) -> None:
        contacts = [{"contact": f"I{i:03d}"} for i in range(10)]
        with self.assertRaises(LadUnsupportedError):
            compile_plan(plan_with({"all": contacts}, [{"op": "OUT", "operand": "O000"}]))


if __name__ == "__main__":
    unittest.main()
