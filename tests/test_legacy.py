from __future__ import annotations

import unittest
from pathlib import Path

from syntec_lad.legacy import parse_lad


SAMPLE = Path(
    r"D:\Toolsyntec\pakeageTool\快速打包工具\DiskC\OpenCNC\Ladder\cnc.lad"
)


@unittest.skipUnless(SAMPLE.exists(), "real cnc.lad sample is unavailable")
class LegacyAdapterTests(unittest.TestCase):
    def test_legacy_parser_loads_and_parses_real_sample(self) -> None:
        result = parse_lad(SAMPLE, encoding="big5")
        self.assertEqual(len(result["rows"]), 466)
        self.assertEqual(len(result["comments"]), 110)
        self.assertEqual(result["integrity"]["issues"], [])


if __name__ == "__main__":
    unittest.main()
