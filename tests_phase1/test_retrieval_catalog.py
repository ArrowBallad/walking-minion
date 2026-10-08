from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.models import AgentDecision

from support import ScriptedClient, make_runtime
from test_runtime_flow import call, final


def payload(messages: list[dict]) -> dict:
    return json.loads(messages[1]["content"])


class RetrievalCatalogTests(unittest.TestCase):
    def test_catalog_survives_after_raw_search_result_leaves_recent_results(self) -> None:
        def verify(messages, _tools) -> AgentDecision:
            current = payload(messages)
            self.assertEqual("get_record", current["recent_tool_results"][0]["tool_name"])
            records = current["retrieval_catalog"]["records"]
            self.assertEqual(["rec-5", "rec-3"], [row["record_id"] for row in records])
            self.assertEqual("索引标题 5", records[0]["title"])
            self.assertEqual("短预览 5", records[0]["short_preview"])
            self.assertNotIn("data", records[0])
            return final()

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "search",
                        "search_records",
                        patient_id="P1",
                        information_need="查找目标索引。",
                        keyword="目标",
                        limit=10,
                    ),
                    call(
                        "patient",
                        "get_patient",
                        patient_id="P1",
                        information_need="确认患者身份。",
                    ),
                    call(
                        "unrelated-read",
                        "get_record",
                        patient_id="P1",
                        information_need="执行另一个后续调用。",
                        record_id="rec-1",
                    ),
                    verify,
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client, recent_result_limit=1)
            state = runtime.run(patient_id="P1", task="审阅目标。", case_id="test")
            self.assertEqual("complete", state.status)

    def test_repeated_search_merges_without_duplicate_catalog_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "search-1",
                        "search_records",
                        patient_id="P1",
                        information_need="第一次查找。",
                        keyword="目标",
                        limit=10,
                    ),
                    call(
                        "search-2",
                        "search_records",
                        patient_id="P1",
                        information_need="重复查找以验证去重。",
                        keyword="目标",
                        limit=10,
                    ),
                    final(),
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅目标。", case_id="test")
            self.assertEqual(["rec-5", "rec-3"], state.discovered_record_ids)
            self.assertEqual(
                state.discovered_record_ids,
                [row["record_id"] for row in state.discovered_records],
            )
            self.assertTrue(all("data" not in row for row in state.discovered_records))

    def test_get_record_changes_catalog_inspected_flag(self) -> None:
        def verify(messages, _tools) -> AgentDecision:
            records = payload(messages)["retrieval_catalog"]["records"]
            flags = {row["record_id"]: row["inspected"] for row in records}
            self.assertTrue(flags["rec-5"])
            self.assertFalse(flags["rec-3"])
            return final("rec-5")

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "search",
                        "search_records",
                        patient_id="P1",
                        information_need="查找目标索引。",
                        keyword="目标",
                        limit=10,
                    ),
                    call(
                        "read",
                        "get_record",
                        patient_id="P1",
                        information_need="读取其中一条全文。",
                        record_id="rec-5",
                    ),
                    verify,
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅目标。", case_id="test")
            self.assertEqual("complete", state.status)

    def test_preview_only_record_cannot_be_final_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "search",
                        "search_records",
                        patient_id="P1",
                        information_need="查找目标索引。",
                        keyword="目标",
                        limit=10,
                    ),
                    final("rec-3"),
                    final("rec-3"),
                    final("rec-3"),
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅目标。", case_id="test")
            self.assertEqual("error", state.status)
            self.assertEqual("invalid_final_evidence", state.stop_reason)
            self.assertEqual([], state.inspected_record_ids)

    def test_catalog_is_bounded_and_reports_omitted_count(self) -> None:
        def verify(messages, _tools) -> AgentDecision:
            catalog = payload(messages)["retrieval_catalog"]
            self.assertEqual(3, catalog["limit"])
            self.assertEqual(3, catalog["included_count"])
            self.assertEqual(6, catalog["total_discovered_count"])
            self.assertEqual(3, catalog["omitted_count"])
            return final()

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "search",
                        "search_records",
                        patient_id="P1",
                        information_need="查找全部索引。",
                        limit=10,
                    ),
                    verify,
                ]
            )
            runtime, _, _ = make_runtime(
                Path(directory), client, retrieval_catalog_limit=3
            )
            state = runtime.run(patient_id="P1", task="审阅。", case_id="test")
            self.assertEqual("complete", state.status)

    def test_first_prompt_catalog_contains_no_unsearched_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([final()])
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅。", case_id="test")
            self.assertEqual("complete", state.status)
            current = payload(client.contexts[0])
            self.assertEqual([], current["retrieval_catalog"]["records"])
            self.assertEqual(0, current["retrieval_catalog"]["total_discovered_count"])
            serialized = json.dumps(current["retrieval_catalog"], ensure_ascii=False)
            self.assertNotIn("索引标题", serialized)
            self.assertNotIn("rec-1", serialized)


if __name__ == "__main__":
    unittest.main()
