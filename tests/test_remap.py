from __future__ import annotations

import unittest

from syntec_lad.compiler import compile_plan
from syntec_lad.errors import LadPatchError, LadValidationError
from syntec_lad.inspection import inspect_document
from syntec_lad.model import LadDocument, encode_comment, iter_comment_blocks
from syntec_lad.opcodes import encode_instruction_cell, make_line_cell
from syntec_lad.remap import (
    remap_plc_bits,
    remap_variable_comment_addresses,
    remap_variable_comments,
)


def small_document() -> LadDocument:
    return compile_plan(
        {
            "schema": "syntec-lad-plan/v1",
            "programs": [
                {
                    "name": "MAIN",
                    "networks": [
                        {
                            "condition": {
                                "all": [
                                    {"contact": "I000"},
                                    {"contact": "I001"},
                                ]
                            },
                            "actions": [{"op": "OUT", "operand": "O000"}],
                        }
                    ],
                }
            ],
        }
    )


def operand_values(document: LadDocument) -> list[str]:
    result: list[str] = []
    for row in inspect_document(document)["rows"]:
        if row.get("kind") != "code":
            continue
        for cell in row.get("cells", []):
            for operand in cell.get("operands", []):
                result.append(str(operand.get("value", "")))
    return result


class RemapTests(unittest.TestCase):
    def test_remap_is_simultaneous_and_does_not_cascade(self) -> None:
        document = small_document()
        result = remap_plc_bits(document, {"I0": "I1", "I1": "I2"})
        self.assertEqual(operand_values(result.document)[:2], ["I001", "I002"])
        self.assertEqual(result.source_counts, {"I000": 1, "I001": 1})
        self.assertEqual(result.changed_records, 1)

        swapped = remap_plc_bits(document, {"I0": "I1", "I1": "I0"})
        self.assertEqual(operand_values(swapped.document)[:2], ["I001", "I000"])

    def test_remap_preserves_flow_strings_and_comments(self) -> None:
        jump = encode_instruction_cell(0xCA, string="I000").ljust(40, b"\x00")
        jsr = encode_instruction_cell(0xCB, string="").ljust(40, b"\x00")
        comment = encode_comment("I0", 1, "gb2312")[0]
        document = LadDocument([comment, jump, jsr], encoding="gb2312")
        result = remap_plc_bits(document, {"I0": "I1"})
        self.assertEqual(result.document.records[1], jump)
        self.assertEqual(result.document.records[2], jsr)
        self.assertEqual(result.document.records[0], comment)
        self.assertEqual(len(result.changes), 0)

    def test_unknown_opcode_and_non_operand_bytes_are_preserved(self) -> None:
        unknown = bytes([0, 0xEE]) + b"I000".ljust(38, b"\x00")
        document = LadDocument([unknown], encoding="gb2312")
        result = remap_plc_bits(document, {"I0": "I1"})
        self.assertEqual(result.document.records[0], unknown)
        self.assertEqual(len(result.changes), 0)

    def test_comment_blocks_are_counted_and_not_rewritten(self) -> None:
        comment = encode_comment("I0 old label", 4, "gb2312")
        line = make_line_cell(0).ljust(40, b"\x00")
        document = LadDocument(comment + [line], encoding="gb2312")
        result = remap_plc_bits(document, {"I0": "I1"})
        self.assertEqual(result.comments_untouched, 1)
        self.assertEqual(result.document.records[: len(comment)], comment)

    def test_remap_skips_all_records_of_multirecord_comment(self) -> None:
        text = "R300.0 \t" + ("測試" * 30) + " \t"
        comment = encode_comment(text, 4, "big5")
        self.assertGreater(len(comment), 1)
        line = make_line_cell(0).ljust(40, b"\x00")
        document = LadDocument(comment + [line], encoding="big5")
        result = remap_plc_bits(document, {"R300.0": "R330.0"})
        self.assertEqual(result.comments_untouched, 1)
        self.assertEqual(
            result.document.records[: len(comment)],
            comment,
        )

    def test_require_all_sources_and_expected_hash(self) -> None:
        document = small_document()
        with self.assertRaises(LadValidationError):
            remap_plc_bits(document, {"I9": "I10"}, require_all_sources=True)
        with self.assertRaises(LadPatchError):
            remap_plc_bits(
                document,
                {"I0": "I1"},
                expected_sha256="0" * 64,
            )

    def test_mapping_validation(self) -> None:
        document = small_document()
        with self.assertRaises(LadValidationError):
            remap_plc_bits(document, {})
        with self.assertRaises(LadValidationError):
            remap_plc_bits(document, {"TM0": "TM1"})
        with self.assertRaises(LadValidationError):
            remap_plc_bits(document, {"I0": "I1", "I000": "I2"})

    def test_comment_remap_preserves_text_and_unrelated_records(self) -> None:
        comment = encode_comment("R300.0 \t接水箱低 \t", 4, "big5")
        line = make_line_cell(0).ljust(40, b"\x00")
        document = LadDocument(comment + [line], encoding="big5")
        result = remap_variable_comments(
            document,
            {"R300.0": "R330.0"},
            require_all_sources=True,
        )
        block = iter_comment_blocks(result.document.records, "big5")[0]
        self.assertEqual(block.text, "R330.0 \t接水箱低 \t")
        self.assertEqual(result.document.records[-1], line)
        self.assertEqual(len(result.document.records), len(document.records))
        self.assertEqual(result.changes[0].before, "R300.0")
        self.assertEqual(result.changes[0].after, "R330.0")

    def test_comment_remap_preserves_non_register_comments(self) -> None:
        comment = encode_comment("A0 \t開機脈沖 \t", 4, "big5")
        document = LadDocument(comment, encoding="big5")
        result = remap_variable_comments(
            document,
            {"R300.0": "R330.0"},
        )
        self.assertEqual(result.document.records, document.records)
        self.assertEqual(len(result.changes), 0)

    def test_comment_remap_rejects_target_collision_and_length_change(self) -> None:
        old = encode_comment("R300.0 \t旧注释 \t", 4, "gb2312")
        target = encode_comment("R330.0 \t已有注释 \t", 4, "gb2312")
        document = LadDocument(old + target, encoding="gb2312")
        with self.assertRaises(LadValidationError):
            remap_variable_comments(document, {"R300.0": "R330.0"})

        result = LadDocument(old, encoding="gb2312")
        with self.assertRaises(LadValidationError):
            remap_variable_comments(result, {"R300.0": "R330.10"})

    def test_comment_remap_requires_all_sources(self) -> None:
        comment = encode_comment("R300.0 \t旧注释 \t", 4, "gb2312")
        document = LadDocument(comment, encoding="gb2312")
        with self.assertRaises(LadValidationError):
            remap_variable_comments(
                document,
                {"R301.0": "R331.0"},
                require_all_sources=True,
            )

    def test_comment_address_remap_handles_o_bits_and_resizes(self) -> None:
        comment = encode_comment("O0 \t通道1選擇 \t", 4, "big5")
        line = make_line_cell(0).ljust(40, b"\x00")
        document = LadDocument(comment + [line], encoding="big5")
        result = remap_variable_comment_addresses(
            document,
            {"O0": "O300"},
            require_all_sources=True,
        )
        block = iter_comment_blocks(result.document.records, "big5")[0]
        self.assertEqual(block.text, "O300 \t通道1選擇 \t")
        self.assertEqual(result.document.records[-1], line)
        self.assertEqual(result.changes[0].before, "O0")
        self.assertEqual(result.changes[0].after, "O300")

    def test_comment_address_remap_handles_r_bits(self) -> None:
        comment = encode_comment("R400.0 \t液壓站 \t", 4, "big5")
        document = LadDocument(comment, encoding="big5")
        result = remap_variable_comment_addresses(
            document,
            {"R400.0": "R470.0"},
            require_all_sources=True,
        )
        block = iter_comment_blocks(result.document.records, "big5")[0]
        self.assertEqual(block.text, "R470.0 \t液壓站 \t")

    def test_comment_address_remap_allows_cross_family_o_to_r(self) -> None:
        comment = encode_comment("O0 \tlabel \t", 4, "gb2312")
        document = LadDocument(comment, encoding="gb2312")
        result = remap_variable_comment_addresses(
            document,
            {"O0": "R470.0"},
            require_all_sources=True,
        )
        block = iter_comment_blocks(result.document.records, "gb2312")[0]
        self.assertEqual(block.text, "R470.0 \tlabel \t")


if __name__ == "__main__":
    unittest.main()
