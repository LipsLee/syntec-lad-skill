from __future__ import annotations

import unittest
from pathlib import Path

from syntec_lad.compiler import compile_networks
from syntec_lad.errors import LadPatchError
from syntec_lad.inspection import inspect_document
from syntec_lad.model import LadDocument
from syntec_lad.native import find_lad_dll, validate_document
from syntec_lad.patch import CHANGE_SCHEMA, apply_change_plan


SAMPLE = Path(r"D:\Toolsyntec\pakeageTool\快速打包工具\DiskC\OpenCNC\Ladder\cnc.lad")


@unittest.skipUnless(SAMPLE.exists(), "real cnc.lad sample is unavailable")
class PatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = LadDocument.load(SAMPLE)
        self.inspection = inspect_document(self.document)

    def _change(self, operation: dict) -> dict:
        return {
            "schema": CHANGE_SCHEMA,
            "base_sha256": self.document.sha256,
            "operations": [operation],
        }

    def test_replace_preserves_unmodified_records(self) -> None:
        candidate = self.inspection["network_candidates"][0]
        network = {
            "condition": {"contact": "I000"},
            "actions": [{"op": "OUT", "operand": "O000"}],
        }
        result = apply_change_plan(
            self.document,
            self._change(
                {
                    "op": "replace",
                    "record_start": candidate["record_start"],
                    "record_end": candidate["record_end"],
                    "expected_sha256": candidate["sha256"],
                    "networks": [network],
                }
            ),
        )
        self.assertEqual(
            result.records[1:],
            self.document.records[1:],
        )
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(result)

    def test_stale_and_structural_targets_are_rejected(self) -> None:
        candidate = self.inspection["network_candidates"][0]
        stale_base = self._change(
            {
                "op": "delete",
                "record_start": candidate["record_start"],
                "record_end": candidate["record_end"],
                "expected_sha256": candidate["sha256"],
            }
        )
        stale_base["base_sha256"] = "0" * 64
        with self.assertRaisesRegex(LadPatchError, "base hash mismatch"):
            apply_change_plan(self.document, stale_base)

        bad = self._change(
            {
                "op": "delete",
                "record_start": candidate["record_start"],
                "record_end": candidate["record_end"],
                "expected_sha256": "0" * 64,
            }
        )
        with self.assertRaises(LadPatchError):
            apply_change_plan(self.document, bad)

        structural = self._change(
            {
                "op": "delete",
                "record_start": 193,
                "record_end": 193,
                "expected_sha256": self.document.expected_hash(193, 193),
            }
        )
        with self.assertRaises(LadPatchError):
            apply_change_plan(self.document, structural)

    def test_overlapping_operations_are_rejected(self) -> None:
        candidate = self.inspection["network_candidates"][0]
        operation = {
            "op": "delete",
            "record_start": candidate["record_start"],
            "record_end": candidate["record_end"],
            "expected_sha256": candidate["sha256"],
        }
        change = {
            "schema": CHANGE_SCHEMA,
            "base_sha256": self.document.sha256,
            "operations": [operation, dict(operation)],
        }
        with self.assertRaisesRegex(LadPatchError, "overlap"):
            apply_change_plan(self.document, change)

    def test_insert_before_replace_at_same_boundary_is_preserved(self) -> None:
        network = {
            "condition": {"contact": "I000"},
            "actions": [{"op": "OUT", "operand": "O000"}],
        }
        inserted_network = {
            "condition": {"contact": "I010"},
            "actions": [{"op": "OUT", "operand": "O010"}],
        }
        change = self._change(
            {
                "op": "replace",
                "record_start": 0,
                "record_end": 0,
                "expected_sha256": self.document.expected_hash(0, 0),
                "networks": [network],
            }
        )
        change["operations"].append(
            {
                "op": "insert",
                "at_record": 0,
                "expected_sha256": self.document.expected_hash(0, 0),
                "networks": [inserted_network],
            }
        )

        result = apply_change_plan(self.document, change)
        inserted_records = compile_networks(
            [inserted_network],
            encoding=self.document.encoding,
        )
        replacement_records = compile_networks(
            [network],
            encoding=self.document.encoding,
        )
        self.assertEqual(result.records[:len(inserted_records)], inserted_records)
        replaced_end = len(inserted_records) + len(replacement_records)
        self.assertEqual(
            result.records[len(inserted_records):replaced_end],
            replacement_records,
        )
        self.assertEqual(
            result.records[replaced_end:],
            self.document.records[1:],
        )

    def test_cross_rung_boundary_is_rejected(self) -> None:
        # This row is part of a vertically connected branch in cnc.lad.
        document = self.document
        start = 19
        end = 19
        change = self._change(
            {
                "op": "delete",
                "record_start": start,
                "record_end": end,
                "expected_sha256": document.expected_hash(start, end),
            }
        )
        with self.assertRaises(LadPatchError):
            apply_change_plan(document, change)

    def test_insert_and_delete(self) -> None:
        candidate = self.inspection["network_candidates"][0]
        network = {
            "condition": {"contact": "I010"},
            "actions": [{"op": "OUT", "operand": "O010"}],
        }
        inserted = apply_change_plan(
            self.document,
            self._change(
                {
                    "op": "insert",
                    "at_record": 1,
                    "expected_sha256": self.document.expected_hash(1, 1),
                    "networks": [network],
                }
            ),
        )
        self.assertEqual(len(inserted.records), len(self.document.records) + 1)
        self.assertEqual(inserted.records[0], self.document.records[0])
        self.assertEqual(inserted.records[2:], self.document.records[1:])
        deleted = apply_change_plan(
            self.document,
            self._change(
                {
                    "op": "delete",
                    "record_start": candidate["record_start"],
                    "record_end": candidate["record_end"],
                    "expected_sha256": candidate["sha256"],
                }
            ),
        )
        self.assertEqual(len(deleted.records), len(self.document.records) - 1)
        start = candidate["record_start"]
        end = candidate["record_end"]
        self.assertEqual(deleted.records[:start], self.document.records[:start])
        self.assertEqual(deleted.records[start:], self.document.records[end + 1:])
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(inserted)
        validate_document(deleted)

    def test_comment_boundaries_are_rejected_or_preserved(self) -> None:
        comment = next(
            item
            for item in self.inspection["comments"]
            if item["record_count"] > 2
        )
        start = comment["record_start"]
        end = comment["record_end"]
        network = {
            "condition": {"contact": "I010"},
            "actions": [{"op": "OUT", "operand": "O010"}],
        }

        inside = self._change(
            {
                "op": "insert",
                "at_record": start + 1,
                "expected_sha256": self.document.expected_hash(start + 1, start + 1),
                "networks": [network],
            }
        )
        with self.assertRaisesRegex(LadPatchError, "inside comment block"):
            apply_change_plan(self.document, inside)

        partial = self._change(
            {
                "op": "delete",
                "record_start": start,
                "record_end": start + 1,
                "expected_sha256": self.document.expected_hash(start, start + 1),
            }
        )
        with self.assertRaisesRegex(LadPatchError, "partially selects comment block"):
            apply_change_plan(self.document, partial)

        before = apply_change_plan(
            self.document,
            self._change(
                {
                    "op": "insert",
                    "at_record": start,
                    "expected_sha256": self.document.expected_hash(start, start),
                    "networks": [network],
                }
            ),
        )
        self.assertEqual(
            before.records[start + 1:end + 2],
            self.document.records[start:end + 1],
        )

    def test_unknown_jump_target_is_rejected(self) -> None:
        candidate = self.inspection["network_candidates"][0]
        with self.assertRaisesRegex(LadPatchError, "existing LAB record"):
            apply_change_plan(
                self.document,
                self._change(
                    {
                        "op": "replace",
                        "record_start": candidate["record_start"],
                        "record_end": candidate["record_end"],
                        "expected_sha256": candidate["sha256"],
                        "networks": [
                            {
                                "condition": {"always": True},
                                "actions": [{"op": "JSR", "target": "NOPE"}],
                            }
                        ],
                    }
                ),
            )


if __name__ == "__main__":
    unittest.main()
