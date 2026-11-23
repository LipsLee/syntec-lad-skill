from __future__ import annotations

import os
import unittest
from pathlib import Path

from syntec_lad.compiler import compile_networks
from syntec_lad.errors import LadPatchError
from syntec_lad.inspection import inspect_document
from syntec_lad.model import LadDocument
from syntec_lad.native import find_lad_dll, validate_document
from syntec_lad.opcodes import STRUCTURAL_OPCODES
from syntec_lad.patch import CHANGE_SCHEMA, apply_change_plan


SAMPLE_ENV = os.environ.get("SYNTEC_LAD_SAMPLE") or os.environ.get("SYNTEC_SAMPLE_LAD")
SAMPLE = Path(SAMPLE_ENV) if SAMPLE_ENV else None


@unittest.skipUnless(
    SAMPLE is not None and SAMPLE.is_file(),
    "real cnc.lad sample is unavailable",
)
class PatchTests(unittest.TestCase):
    def setUp(self) -> None:
        assert SAMPLE is not None
        self.document = LadDocument.load(SAMPLE)
        self.inspection = inspect_document(self.document)

    def _first_candidate(self) -> dict:
        return self.inspection["network_candidates"][0]

    def _multi_row_candidate(self) -> dict:
        return next(
            candidate
            for candidate in self.inspection["network_candidates"]
            if candidate["record_count"] >= 2
        )

    def _structural_record(self) -> int:
        return next(
            index
            for index, record in enumerate(self.document.records)
            if record[1] in STRUCTURAL_OPCODES
        )

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
        replacement_records = compile_networks(
            [network],
            encoding=self.document.encoding,
        )
        start = candidate["record_start"]
        end = candidate["record_end"]
        offset = len(replacement_records) - (end - start + 1)
        self.assertEqual(result.records[:start], self.document.records[:start])
        self.assertEqual(
            result.records[start + len(replacement_records) :],
            self.document.records[end + 1 :],
        )
        self.assertEqual(
            len(result.records),
            len(self.document.records) + offset,
        )
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        validate_document(result)

    def test_stale_and_structural_targets_are_rejected(self) -> None:
        candidate = self.inspection["network_candidates"][0]
        structural_record = self._structural_record()
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
                "record_start": structural_record,
                "record_end": structural_record,
                "expected_sha256": self.document.expected_hash(
                    structural_record, structural_record
                ),
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
        candidate = self._first_candidate()
        anchor = candidate["record_start"]
        change = self._change(
            {
                "op": "replace",
                "record_start": candidate["record_start"],
                "record_end": candidate["record_end"],
                "expected_sha256": candidate["sha256"],
                "networks": [network],
            }
        )
        change["operations"].append(
            {
                "op": "insert",
                "at_record": anchor,
                "expected_sha256": self.document.expected_hash(anchor, anchor),
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
        self.assertEqual(result.records[:anchor], self.document.records[:anchor])
        head = anchor + len(inserted_records)
        self.assertEqual(
            result.records[anchor:head],
            inserted_records,
        )
        replaced_end = head + len(replacement_records)
        self.assertEqual(
            result.records[head:replaced_end],
            replacement_records,
        )
        self.assertEqual(
            result.records[replaced_end:],
            self.document.records[candidate["record_end"] + 1 :],
        )

    def test_cross_rung_boundary_is_rejected(self) -> None:
        # Deleting a single row out of a multi-row network must be rejected:
        # the network's rows are vertically connected, so removing one leaves
        # a dangling half of the rung.
        document = self.document
        candidate = self._multi_row_candidate()
        start = candidate["record_start"] + 1
        end = start
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
        anchor = candidate["record_start"]
        inserted = apply_change_plan(
            self.document,
            self._change(
                {
                    "op": "insert",
                    "at_record": anchor,
                    "expected_sha256": self.document.expected_hash(anchor, anchor),
                    "networks": [network],
                }
            ),
        )
        inserted_records = compile_networks(
            [network],
            encoding=self.document.encoding,
        )
        offset = len(inserted_records)
        self.assertEqual(len(inserted.records), len(self.document.records) + offset)
        self.assertEqual(inserted.records[:anchor], self.document.records[:anchor])
        self.assertEqual(inserted.records[anchor : anchor + offset], inserted_records)
        self.assertEqual(inserted.records[anchor + offset :], self.document.records[anchor:])
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
        start = candidate["record_start"]
        end = candidate["record_end"]
        self.assertEqual(
            len(deleted.records),
            len(self.document.records) - (end - start + 1),
        )
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
