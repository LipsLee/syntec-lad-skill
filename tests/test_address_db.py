from __future__ import annotations

import unittest

from syntec_lad.address_db import (
    default_address_db_path,
    load_address_db,
    lookup_address,
)
from syntec_lad.errors import LadValidationError


class AddressDbTests(unittest.TestCase):
    def test_database_is_bundled(self) -> None:
        path = default_address_db_path()
        self.assertTrue(path.is_file())
        index = load_address_db()
        self.assertEqual(len(index), 602)

    def test_exact_short_and_padded_addresses(self) -> None:
        for query, expected in (
            ("C6", "C6"),
            ("C006", "C6"),
            ("S29", "S29"),
            ("S029", "S29"),
            ("R600.1", "R600.1"),
            ("R0600.01", "R600.1"),
        ):
            with self.subTest(query=query):
                hits = lookup_address(query)
                self.assertTrue(hits)
                self.assertEqual(hits[0]["address"], expected)

    def test_keyword_search(self) -> None:
        hits = lookup_address("M码")
        self.assertTrue(hits)
        self.assertTrue(
            any("M码" in description for hit in hits for description in hit["descriptions"])
        )

    def test_unknown_address_returns_no_hits(self) -> None:
        self.assertEqual(lookup_address("R99999"), [])

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaises(LadValidationError):
            lookup_address("  ")


if __name__ == "__main__":
    unittest.main()
