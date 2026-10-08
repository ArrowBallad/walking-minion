from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer.storage import MockDatabase, initialize_case_instance

from support import write_fixture


class StorageTests(unittest.TestCase):
    def test_old_and_new_records_remain_distinct_and_keep_clinical_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _ = write_fixture(Path(directory))
            records = MockDatabase(database).search_records("P-001", record_type="note")

            self.assertEqual(["rec-old", "rec-new"], [row["record_id"] for row in records])
            self.assertEqual("2024-01-01T00:00:00Z", records[0]["clinical_time"])
            self.assertEqual("2025-01-01T00:00:00Z", records[1]["clinical_time"])

    def test_case_seed_is_copied_to_a_separate_mutable_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, _, _ = write_fixture(root / "seed_parent")
            seed = database
            paths = initialize_case_instance(seed, root / "work", "case-a")

            (paths.database / "orders.jsonl").write_text("changed\n", encoding="utf-8")
            self.assertEqual("", (seed / "orders.jsonl").read_text(encoding="utf-8"))
            self.assertNotEqual(paths.database, paths.memory.parent)


if __name__ == "__main__":
    unittest.main()

