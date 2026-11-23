from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntec_lad.compiler import compile_plan
from syntec_lad.lad_diff import diff_documents, render_diff
from syntec_lad.model import LadDocument


def _plan(networks: list[dict]) -> dict:
    return {
        "schema": "syntec-lad-plan/v1",
        "encoding": "gb2312",
        "programs": [{"name": "MAIN", "networks": networks}],
    }


def _network(operand: str, contact: str = "I000") -> dict:
    return {
        "condition": {"contact": contact},
        "actions": [{"op": "OUT", "operand": operand}],
    }


class DiffTests(unittest.TestCase):
    def test_identical_files_report_same(self) -> None:
        document = compile_plan(_plan([_network("O000")]))
        result = diff_documents(document, document)
        self.assertTrue(result["same"])
        self.assertEqual(result["entries"], [])

    def test_operand_change_is_reported_symbolically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left_path = Path(tmp) / "left.lad"
            right_path = Path(tmp) / "right.lad"
            compile_plan(_plan([_network("O000")])).save(left_path)
            compile_plan(_plan([_network("O001")])).save(right_path)
            left = LadDocument.load(left_path)
            right = LadDocument.load(right_path)

        result = diff_documents(left, right)
        self.assertFalse(result["same"])
        modified = [entry for entry in result["entries"] if entry["status"] == "modified"]
        self.assertEqual(len(modified), 1)
        changes = modified[0]["operand_changes"]
        self.assertTrue(changes)
        self.assertEqual(changes[0]["kind"], "operand")
        self.assertIn("O000", changes[0]["left"])
        self.assertIn("O001", changes[0]["right"])

    def test_added_network_is_reported_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left_path = Path(tmp) / "left.lad"
            right_path = Path(tmp) / "right.lad"
            compile_plan(_plan([_network("O000")])).save(left_path)
            compile_plan(
                _plan([_network("O000"), _network("O002", contact="I001")])
            ).save(right_path)
            left = LadDocument.load(left_path)
            right = LadDocument.load(right_path)

        result = diff_documents(left, right)
        self.assertEqual(result["summary"]["added"], 1)
        self.assertEqual(result["summary"]["modified"], 0)
        self.assertEqual(result["summary"]["removed"], 0)

    def test_text_render_shows_operand_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left_path = Path(tmp) / "left.lad"
            right_path = Path(tmp) / "right.lad"
            compile_plan(_plan([_network("O000")])).save(left_path)
            compile_plan(_plan([_network("O001")])).save(right_path)
            result = diff_documents(
                LadDocument.load(left_path), LadDocument.load(right_path)
            )

        text = render_diff(result, context=5)
        self.assertIn("Modified", text)
        self.assertIn("->", text)
        self.assertIn("OUT O000", text)
        self.assertIn("OUT O001", text)


if __name__ == "__main__":
    unittest.main()
