from __future__ import annotations

import unittest

from syntec_lad.plan_lint import lint_plan, render_plan_lint


def _plan(networks: list[dict]) -> dict:
    return {
        "schema": "syntec-lad-plan/v1",
        "programs": [{"name": "MAIN", "networks": networks}],
    }


def _out(operand: str) -> dict:
    return {"op": "OUT", "operand": operand}


def _rules(result: dict) -> list[str]:
    return [item["rule"] for item in result["issues"]]


class PlanLintTests(unittest.TestCase):
    def test_different_conditions_pass(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {"contact": "I000"},
                        "actions": [_out("O001")],
                    },
                    {
                        "condition": {"contact": "I001"},
                        "actions": [_out("O002")],
                    },
                ]
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])

    def test_reordered_condition_is_mergeable(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {
                            "all": [
                                {"contact": "I000"},
                                {
                                    "any": [
                                        {"contact": "I001"},
                                        {"contact": "I002"},
                                    ]
                                },
                            ]
                        },
                        "actions": [_out("O001")],
                    },
                    {
                        "condition": {
                            "all": [
                                {
                                    "any": [
                                        {"contact": "I002"},
                                        {"contact": "I001"},
                                    ]
                                },
                                {"contact": "I000"},
                            ]
                        },
                        "actions": [_out("O002")],
                    },
                ]
            )
        )
        self.assertIn("mergeable_condition", _rules(result))

    def test_distributed_condition_is_mergeable(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {
                            "all": [
                                {"contact": "I000"},
                                {
                                    "any": [
                                        {"contact": "I001"},
                                        {"contact": "I002"},
                                    ]
                                },
                            ]
                        },
                        "actions": [_out("O001")],
                    },
                    {
                        "condition": {
                            "any": [
                                {
                                    "all": [
                                        {"contact": "I002"},
                                        {"contact": "I000"},
                                    ]
                                },
                                {
                                    "all": [
                                        {"contact": "I000"},
                                        {"contact": "I001"},
                                    ]
                                },
                            ]
                        },
                        "actions": [_out("O002")],
                    },
                ]
            )
        )
        self.assertIn("mergeable_condition", _rules(result))

    def test_unconditional_networks_are_mergeable(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {"always": True},
                        "actions": [_out("O001")],
                    },
                    {
                        "condition": {"always": True},
                        "actions": [_out("O002")],
                    },
                ]
            )
        )
        self.assertIn("mergeable_condition", _rules(result))
        self.assertIn("无条件", result["issues"][0]["detail"])

    def test_mixed_action_width_is_mergeable_when_condition_fits(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {"contact": "I000"},
                        "actions": [_out("O001")],
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
        )
        self.assertIn("mergeable_condition", _rules(result))
        self.assertNotIn("mergeable_condition_too_wide", _rules(result))

    def test_mixed_widths_inside_one_network_are_clean_when_condition_fits(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {"contact": "I000"},
                        "actions": [
                            _out("O001"),
                            {
                                "op": "TIM",
                                "timer": "TM000",
                                "preset": "#10",
                            },
                        ],
                    }
                ]
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])

    def test_wide_condition_cannot_share_a_mixed_action_bus(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {
                            "all": [
                                {"contact": f"I{index:03d}"}
                                for index in range(1, 10)
                            ]
                        },
                        "actions": [
                            _out("O001"),
                            {
                                "op": "TIM",
                                "timer": "TM000",
                                "preset": "#10",
                            },
                        ],
                    }
                ]
            )
        )
        self.assertIn("mixed_action_too_wide", _rules(result))

    def test_flow_action_is_not_reported_as_mergeable(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {"contact": "I000"},
                        "actions": [{"op": "MSG", "target": "HELLO"}],
                    },
                    {
                        "condition": {"contact": "I000"},
                        "actions": [_out("O001")],
                    },
                ]
            )
        )
        self.assertNotIn("mergeable_condition", _rules(result))
        self.assertIn("flow_action_not_mergeable", _rules(result))

    def test_render_mentions_rule_names(self) -> None:
        result = lint_plan(
            _plan(
                [
                    {
                        "condition": {"contact": "I000"},
                        "actions": [_out("O001")],
                    },
                    {
                        "condition": {"contact": "I000"},
                        "actions": [_out("O002")],
                    },
                ]
            )
        )
        text = render_plan_lint(result)
        self.assertIn("mergeable_condition", text)
        self.assertIn("N0001/N0002", text)


if __name__ == "__main__":
    unittest.main()
