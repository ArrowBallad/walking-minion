from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.storage import MockDatabase

from support import write_fixture


class IndexAndRecordTests(unittest.TestCase):
    def test_search_returns_index_only_and_get_record_returns_full_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _ = write_fixture(Path(directory))
            db = MockDatabase(database)
            page = db.search_records("P1", limit=2)

            self.assertEqual(2, page["returned_count"])
            self.assertTrue(page["has_more"])
            self.assertNotIn("data", page["records"][0])
            self.assertNotIn("仅全文可见", str(page))

            full = db.get_record("P1", page["records"][0]["record_id"])
            self.assertIn("data", full)
            self.assertIn("仅全文可见", full["data"]["full_text"])

    def test_pagination_is_stable_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _ = write_fixture(Path(directory))
            db = MockDatabase(database)
            first = db.search_records("P1", limit=2)
            second = db.search_records("P1", limit=2, cursor=first["next_cursor"])
            third = db.search_records("P1", limit=2, cursor=second["next_cursor"])
            ids = [
                row["record_id"]
                for page in (first, second, third)
                for row in page["records"]
            ]

            self.assertEqual(6, len(ids))
            self.assertEqual(6, len(set(ids)))
            self.assertFalse(third["has_more"])

    def test_keyword_and_type_filters_apply_to_index_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _ = write_fixture(Path(directory))
            db = MockDatabase(database)
            page = db.search_records("P1", record_type="note", keyword="目标", limit=10)
            self.assertEqual(["rec-5", "rec-3"], [row["record_id"] for row in page["records"]])


if __name__ == "__main__":
    unittest.main()

