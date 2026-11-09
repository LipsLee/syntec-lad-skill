from __future__ import annotations

import os
import unittest
from pathlib import Path

from syntec_lad.errors import LadValidationError
from syntec_lad.inspection import decode_cell, inspect_document
from syntec_lad.model import LadDocument, detect_encoding, encode_comment, read_comment
from syntec_lad.opcodes import (
    encode_instruction_cell,
    make_structural_row,
    parse_const,
    parse_counter,
    parse_plc_device,
    parse_register,
    parse_timer,
)


SAMPLE = Path(
    os.environ.get(
        "SYNTEC_SAMPLE_LAD",
        r"D:\Toolsyntec\pakeageTool\快速打包工具\DiskC\OpenCNC\Ladder\cnc.lad",
    )
)


class CodecTests(unittest.TestCase):
    def test_real_file_round_trip(self) -> None:
        if not SAMPLE.exists():
            self.skipTest("real cnc.lad sample is unavailable")
        data = SAMPLE.read_bytes()
        document = LadDocument.load(SAMPLE)
        self.assertEqual(document.to_bytes(), data)
        self.assertEqual(len(document.records), 532)
        self.assertEqual(document.encoding, "big5")
        self.assertEqual(document.version, 2)
        report = inspect_document(document)
        self.assertEqual(len(report["comments"]), 110)
        self.assertEqual(report["integrity"]["issues"], [])
        self.assertEqual(len(report["network_candidates"]), 220)

    def test_comment_round_trip_gb2312(self) -> None:
        records = encode_comment("主程序启动", 1, "gb2312")
        block = read_comment(records, 0, "gb2312")
        self.assertEqual(block.text, "主程序启动")
        self.assertEqual(block.comment_type, 1)

    def test_comment_crosses_physical_record(self) -> None:
        text = "A" * 40
        records = encode_comment(text, 4, "gb2312")
        self.assertGreater(len(records), 1)
        block = read_comment(records, 0, "gb2312")
        self.assertEqual(block.text, text)
        self.assertEqual(block.record_count, len(records))

    def test_comment_length_limit(self) -> None:
        with self.assertRaises(LadValidationError):
            encode_comment("A" * 256, 1, "gb2312")
        with self.assertRaises(LadValidationError):
            encode_comment("A", 1, "not-a-codec")

    def test_unknown_document_encoding_is_rejected(self) -> None:
        data = bytes(40) + b"\x00\xFE"
        with self.assertRaises(LadValidationError):
            LadDocument.from_bytes(data, encoding="not-a-codec")

    def test_big5_comment_detection(self) -> None:
        records = encode_comment("雷射加工", 1, "big5")
        self.assertEqual(detect_encoding(records), "big5")

    def test_gb2312_comment_detection(self) -> None:
        records = encode_comment("主程序启动", 1, "gb2312")
        self.assertEqual(detect_encoding(records), "gb2312")

    def test_operand_boundaries(self) -> None:
        self.assertEqual(parse_plc_device("I511").raw, 511)
        self.assertEqual(parse_plc_device("O511").raw, 0x3FF)
        self.assertEqual(parse_plc_device("R0128.31").raw, 0x201F)
        self.assertEqual(parse_register("@R65535").raw, 65535)
        self.assertTrue(parse_register("@R65535").indirect)
        self.assertEqual(parse_const("#-32768").raw, -32768)
        self.assertEqual(parse_timer("TM255").raw, 255)
        self.assertEqual(parse_counter("CT255").raw, 255)
        with self.assertRaises(LadValidationError):
            parse_timer("TM256")
        with self.assertRaises(LadValidationError):
            parse_plc_device("I512")
        with self.assertRaises(LadValidationError):
            parse_const("#32768")

    def test_compare_and_indirect_encoding(self) -> None:
        cell = encode_instruction_cell(
            0x6E,
            op1=parse_const("#5"),
            op2=parse_register("R1"),
        )
        self.assertEqual(len(cell), 8)
        record = cell.ljust(40, b"\x00")
        decoded = decode_cell(record, 0, encoding="gb2312")
        self.assertEqual(decoded["mnemonic"], "CMPE")
        self.assertEqual([item["value"] for item in decoded["operands"]], ["#00005", "R0001"])
        self.assertEqual(cell[6], 0)

        indirect = encode_instruction_cell(
            0x72,
            op1=parse_register("@R1"),
            op2=parse_register("@R2"),
        )
        record = indirect.ljust(40, b"\x00")
        decoded = decode_cell(record, 0, encoding="gb2312")
        self.assertEqual([item["value"] for item in decoded["operands"]], ["@R0001", "@R0002"])
        self.assertEqual(indirect[6], 3)

    def test_structural_rows(self) -> None:
        rts = make_structural_row(0xD0)
        self.assertEqual(rts[1], 0xD0)
        self.assertEqual([rts[slot * 4 + 1] for slot in range(1, 10)], [0x0D] * 9)
        lab = make_structural_row(0xD5, "SUB1")
        self.assertEqual(lab[1], 0xD5)
        self.assertEqual(lab[2:7], b"SUB1\x00")
        self.assertEqual([lab[slot * 4 + 1] for slot in range(2, 10)], [0x0D] * 8)

    def test_unknown_binary_record_preserved(self) -> None:
        # A code row with an unrecognised opcode is still a lossless record.
        record = bytes([0, 0xEE]) + bytes(38)
        document = LadDocument([record], encoding="gb2312")
        self.assertEqual(document.to_bytes(), record + b"\x00\xFE")


if __name__ == "__main__":
    unittest.main()
