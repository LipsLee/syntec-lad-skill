from __future__ import annotations

import json
import unittest
from pathlib import Path

from syntec_lad.compiler import compile_plan
from syntec_lad.inspection import inspect_document
from syntec_lad.model import LadDocument
from syntec_lad.native import find_lad_dll, validate_native


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class ExampleFixtureTests(unittest.TestCase):
    def test_reviewed_plans_rebuild_checked_in_ladders(self) -> None:
        for name in ("demo", "mvp"):
            with self.subTest(name=name):
                plan = json.loads(
                    (EXAMPLES / f"{name}.plan.json").read_text(encoding="utf-8")
                )
                document = compile_plan(plan)
                fixture = LadDocument.load(EXAMPLES / f"{name}.lad")
                self.assertEqual(document.to_bytes(), fixture.to_bytes())
                report = inspect_document(fixture)
                self.assertEqual(report["integrity"]["issues"], [])
                self.assertGreater(report["statistics"]["instruction_cells"], 0)

    def test_checked_in_ladders_pass_native_syntax_check(self) -> None:
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        for name in ("demo", "mvp"):
            with self.subTest(name=name):
                result = validate_native(EXAMPLES / f"{name}.lad")
                self.assertTrue(result["passed"])
                self.assertEqual(result["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
