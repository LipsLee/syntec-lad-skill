from __future__ import annotations

import os
import unittest
from pathlib import Path

from syntec_lad.legacy import parse_lad


SAMPLE_ENV = os.environ.get("SYNTEC_SAMPLE_LAD") or os.environ.get("SYNTEC_LAD_SAMPLE")
SAMPLE = Path(SAMPLE_ENV) if SAMPLE_ENV else None


@unittest.skipUnless(
    SAMPLE is not None and SAMPLE.is_file(),
    "real cnc.lad sample is unavailable",
)
class LegacyAdapterTests(unittest.TestCase):
    def test_legacy_parser_loads_and_parses_real_sample(self) -> None:
        assert SAMPLE is not None
        result = parse_lad(SAMPLE, encoding="big5")
        self.assertEqual(len(result["rows"]), 466)
        self.assertEqual(len(result["comments"]), 110)
        self.assertEqual(result["integrity"]["issues"], [])


if __name__ == "__main__":
    unittest.main()
