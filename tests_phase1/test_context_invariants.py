from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.models import RunState
from clinical_agent_explorer_phase1.storage import MockDatabase
from clinical_agent_explorer_phase1.tools import ToolError, ToolRegistry

from support import write_fixture


def context_args(ref: str) -> dict:
    return {
        "patient_id": "P1",
        "information_need": "保存已读取全文的重要事实。",
        "known_facts": [
            {
                "statement": "一条基于全文的事实。",
                "evidence_refs": [ref],
                "clinical_time": "2026-01-03T00:00:00Z",
                "temporal_status": "current",
            }
        ],
        "unresolved_questions": ["仍需确认什么？"],
        "conflicts": [],
        "relevant_record_refs": [ref],
    }


class ContextInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database, _, _ = write_fixture(Path(self.temporary.name))
        self.tools = ToolRegistry(MockDatabase(database))
        self.state = RunState(run_id="test", patient_id="P1", task="test")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_discovered_preview_cannot_become_known_fact(self) -> None:
        self.state.discovered_record_ids.append("rec-3")
        with self.assertRaisesRegex(ToolError, "未读取全文"):
            self.tools.execute("update_working_context", context_args("rec-3"), self.state)

    def test_inspected_record_can_support_known_fact(self) -> None:
        self.state.discovered_record_ids.append("rec-3")
        self.state.inspected_record_ids.append("rec-3")
        result = self.tools.execute("update_working_context", context_args("rec-3"), self.state)
        self.assertEqual("一条基于全文的事实。", result["patient_context"]["known_facts"][0]["statement"])

    def test_conflict_requires_two_inspected_records(self) -> None:
        self.state.discovered_record_ids.extend(["rec-3", "rec-5"])
        self.state.inspected_record_ids.extend(["rec-3", "rec-5"])
        arguments = context_args("rec-3")
        arguments["conflicts"] = [
            {"statement": "两条记录冲突。", "evidence_refs": ["rec-3", "rec-5"]}
        ]
        result = self.tools.execute("update_working_context", arguments, self.state)
        self.assertEqual(2, len(result["patient_context"]["conflicts"][0]["evidence_refs"]))

    def test_discovered_previews_cannot_support_conflict(self) -> None:
        self.state.discovered_record_ids.extend(["rec-3", "rec-5"])
        arguments = context_args("rec-3")
        arguments["known_facts"] = []
        arguments["conflicts"] = [
            {"statement": "只有索引预览的两条记录似乎冲突。", "evidence_refs": ["rec-3", "rec-5"]}
        ]
        arguments["relevant_record_refs"] = ["rec-3", "rec-5"]
        with self.assertRaisesRegex(ToolError, "未读取全文"):
            self.tools.execute("update_working_context", arguments, self.state)

    def test_write_evidence_must_be_inspected(self) -> None:
        self.state.discovered_record_ids.append("rec-3")
        with self.assertRaisesRegex(ToolError, "未读取全文"):
            self.tools.execute(
                "create_order",
                {
                    "patient_id": "P1",
                    "information_need": "测试写入证据边界。",
                    "order_type": "模拟随访",
                    "reason": "测试",
                    "evidence_refs": ["rec-3"],
                },
                self.state,
            )


if __name__ == "__main__":
    unittest.main()
