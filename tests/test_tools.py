from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer.models import RunState
from clinical_agent_explorer.storage import MockDatabase
from clinical_agent_explorer.tools import ToolError, ToolRegistry

from support import write_fixture


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database, _, _ = write_fixture(Path(self.temporary.name))
        self.tools = ToolRegistry(MockDatabase(database))
        self.state = RunState(run_id="test", patient_id="P-001", task="test")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_patient_scope_and_unknown_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(ToolError, "绑定患者不一致"):
            self.tools.execute("get_patient", {"patient_id": "P-999"}, self.state)
        with self.assertRaisesRegex(ToolError, "未知字段"):
            self.tools.execute(
                "get_patient", {"patient_id": "P-001", "path": "secret"}, self.state
            )

    def test_empty_results_are_explicit(self) -> None:
        result = self.tools.execute(
            "search_records",
            {"patient_id": "P-001", "record_type": "lab"},
            self.state,
        )
        self.assertEqual({"ok": True, "count": 0, "records": []}, result)

    def test_order_receipt_requires_independent_read_back(self) -> None:
        self.state.retrieved_record_ids.append("rec-new")
        created = self.tools.execute(
            "create_order",
            {
                "patient_id": "P-001",
                "order_type": "synthetic-follow-up",
                "reason": "scripted infrastructure test",
                "evidence_refs": ["rec-new"],
            },
            self.state,
        )
        self.assertIn("receipt", created)
        self.assertNotIn("verified", created["receipt"])

        read_back = self.tools.execute(
            "get_order",
            {"patient_id": "P-001", "order_id": created["receipt"]["order_id"]},
            self.state,
        )
        self.assertTrue(read_back["found"])
        self.assertEqual(created["order"], read_back["order"])

    def test_write_cannot_cite_unretrieved_records(self) -> None:
        with self.assertRaisesRegex(ToolError, "未在本次运行中检索"):
            self.tools.execute(
                "create_order",
                {
                    "patient_id": "P-001",
                    "order_type": "synthetic-follow-up",
                    "reason": "test",
                    "evidence_refs": ["rec-new"],
                },
                self.state,
            )


if __name__ == "__main__":
    unittest.main()
