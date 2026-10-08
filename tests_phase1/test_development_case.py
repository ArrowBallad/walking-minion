from __future__ import annotations

import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.storage import MockDatabase, read_jsonl


class DevelopmentCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = Path("cases/phase1_development/gi_longitudinal_001")
        self.database = MockDatabase(self.case)

    def test_case_has_expected_scale_without_embedding_gold(self) -> None:
        records = read_jsonl(self.case / "clinical_records.jsonl")
        self.assertGreaterEqual(len(records), 30)
        self.assertLessEqual(len(records), 50)
        self.assertTrue(all("data" in record for record in records))

    def test_broad_search_is_paginated_and_does_not_return_full_records(self) -> None:
        page = self.database.search_records("GI-LONG-001", limit=10)
        self.assertEqual(10, page["returned_count"])
        self.assertTrue(page["has_more"])
        self.assertTrue(all("data" not in record for record in page["records"]))

    def test_procedure_and_report_are_separate_records(self) -> None:
        procedure = self.database.get_record("GI-LONG-001", "long-rec-002")
        report = self.database.get_record("GI-LONG-001", "long-rec-003")
        self.assertEqual("procedure", procedure["record_type"])
        self.assertEqual("report", report["record_type"])
        self.assertNotIn("stomach", procedure["data"])
        self.assertIn("stomach", report["data"])


if __name__ == "__main__":
    unittest.main()

