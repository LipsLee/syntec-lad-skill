from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from syntec_lad.compiler import compile_plan
from syntec_lad.errors import LadError
from syntec_lad.lint import lint_document, render_lint
from syntec_lad.model import LadDocument


def _plan(networks: list[dict], *, encoding: str = "gb2312") -> dict:
    return {
        "schema": "syntec-lad-plan/v1",
        "encoding": encoding,
        "programs": [{"name": "MAIN", "networks": networks}],
    }


def _document(networks: list[dict]) -> LadDocument:
    return compile_plan(_plan(networks))


def _rules(result: dict) -> list[str]:
    return [item["rule"] for item in result["issues"]]


class LintTests(unittest.TestCase):
    def test_clean_program_passes(self) -> None:
        document = _document(
            [
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O000"}],
                }
            ]
        )
        result = lint_document(document)
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["error"], 0)

    def test_duplicate_coil_is_reported(self) -> None:
        document = _document(
            [
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
                {
                    "condition": {"contact": "I002"},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
            ]
        )
        result = lint_document(document)
        self.assertIn("duplicate_coil", _rules(result))
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("O001" in item["detail"] for item in result["issues"])
        )

    def test_mergeable_condition_is_reported(self) -> None:
        document = _document(
            [
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O002"}],
                },
            ]
        )
        result = lint_document(document)
        self.assertIn("mergeable_condition", _rules(result))

    def test_unconditional_networks_are_mergeable(self) -> None:
        document = _document(
            [
                {
                    "condition": {"always": True},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
                {
                    "condition": {"always": True},
                    "actions": [{"op": "OUT", "operand": "O002"}],
                },
            ]
        )
        result = lint_document(document)
        self.assertIn("mergeable_condition", _rules(result))

    def test_mixed_action_width_is_reported_as_mergeable_when_condition_fits(self) -> None:
        document = _document(
            [
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
                {
                    "condition": {"contact": "I000"},
                    "actions": [
                        {
                            "op": "TIM",
                            "timer": "TM000",
                            "preset": "#10",
                        }
                    ],
                },
            ]
        )
        result = lint_document(document)
        self.assertIn("mergeable_condition", _rules(result))
        self.assertNotIn("mergeable_condition_too_wide", _rules(result))

    def test_flow_action_is_not_reported_as_mergeable(self) -> None:
        document = _document(
            [
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "MSG", "target": "HELLO"}],
                },
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
            ]
        )
        result = lint_document(document)
        self.assertNotIn("mergeable_condition", _rules(result))
        self.assertIn("flow_action_not_mergeable", _rules(result))

    def test_s_used_as_coil_is_reported(self) -> None:
        try:
            document = _document(
                [
                    {
                        "condition": {"contact": "I000"},
                        "actions": [{"op": "OUT", "operand": "S010"}],
                    }
                ]
            )
        except LadError as exc:  # 编译器本身可能拒绝，属于更强的保护
            self.skipTest(f"compiler rejects S coil: {exc}")
        result = lint_document(document)
        self.assertIn("s_as_coil", _rules(result))

    def test_simplified_comment_is_reported(self) -> None:
        document = _document(
            [
                {
                    "comment": "主轴润滑压力不足",
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O000"}],
                }
            ]
        )
        result = lint_document(document)
        self.assertIn("simplified_comment", _rules(result))

    def test_render_output_mentions_rule_names(self) -> None:
        document = _document(
            [
                {
                    "condition": {"contact": "I000"},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
                {
                    "condition": {"contact": "I002"},
                    "actions": [{"op": "OUT", "operand": "O001"}],
                },
            ]
        )
        text = render_lint(lint_document(document))
        self.assertIn("duplicate_coil", text)
        self.assertIn("FAIL", text)

    def test_io_reuse_threshold(self) -> None:
        networks = [
            {
                "condition": {"contact": "I005"},
                "actions": [{"op": "OUT", "operand": f"O{index:03d}"}],
            }
            for index in range(3)
        ]
        document = _document(networks)
        strict = lint_document(document, io_max_uses=2)
        loose = lint_document(document, io_max_uses=9)
        self.assertIn("io_reused", _rules(strict))
        self.assertNotIn("io_reused", _rules(loose))

    def test_real_sample_lints_without_crashing(self) -> None:
        sample_env = os.environ.get("SYNTEC_SAMPLE_LAD") or os.environ.get(
            "SYNTEC_LAD_SAMPLE"
        )
        sample = Path(sample_env) if sample_env else None
        if sample is None or not sample.is_file():
            self.skipTest("real cnc.lad sample is unavailable")
        assert sample is not None
        result = lint_document(LadDocument.load(sample))
        self.assertGreater(result["source"]["networks"], 100)
        self.assertEqual(
            sum(result["summary"].values()),
            len(result["issues"]),
        )


if __name__ == "__main__":
    unittest.main()
