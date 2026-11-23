from __future__ import annotations

import unittest

from syntec_lad.zhconv import (
    SIMPLE_TO_TRAD,
    find_simplified,
    has_simplified,
    to_simplified,
    to_traditional,
)


class ZhconvTests(unittest.TestCase):
    def test_convert_simplified_to_traditional(self) -> None:
        self.assertEqual(to_traditional("主轴润滑压力不足"), "主軸潤滑壓力不足")
        self.assertEqual(to_traditional("安全门打开"), "安全門打開")
        self.assertEqual(to_traditional("请检查油位"), "請檢查油位")

    def test_convert_traditional_to_simplified(self) -> None:
        self.assertEqual(to_simplified("主軸潤滑壓力不足"), "主轴润滑压力不足")
        self.assertEqual(to_simplified("安全門警報"), "安全门警报")

    def test_neutral_text_is_unchanged(self) -> None:
        text = "R590.10 / LD I013 / OUT C062"
        self.assertEqual(to_traditional(text), text)
        self.assertFalse(has_simplified(text))

    def test_find_simplified_reports_positions(self) -> None:
        hits = find_simplified("门轴报警")
        self.assertEqual([item["simplified"] for item in hits], ["门", "轴", "报"])
        self.assertEqual(hits[0]["traditional"], "門")
        self.assertEqual(hits[0]["index"], 0)

    def test_round_trip_is_lossless_for_covered_characters(self) -> None:
        original = "主軸潤滑壓力不足"
        self.assertEqual(to_traditional(to_simplified(original)), original)

    def test_table_entries_are_consistent(self) -> None:
        for simplified, traditional in SIMPLE_TO_TRAD.items():
            self.assertEqual(len(simplified), 1)
            self.assertEqual(len(traditional), 1)


if __name__ == "__main__":
    unittest.main()
