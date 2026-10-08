from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.models import AgentDecision, ToolCall
from clinical_agent_explorer_phase1.storage import read_jsonl
from clinical_agent_explorer_phase1.tools import ToolRegistry

from support import ScriptedClient, make_runtime
from test_runtime_flow import call, final


def invalid_context(call_id: str) -> AgentDecision:
    return call(
        call_id,
        "update_working_context",
        patient_id="P1",
        information_need="尝试保存只有一侧证据的冲突。",
        known_facts=[],
        unresolved_questions=["另一侧证据是否存在？"],
        conflicts=[
            {
                "statement": "两条记录的状态可能冲突。",
                "evidence_refs": ["rec-5"],
            }
        ],
        relevant_record_refs=["rec-5"],
    )


class RecoverableToolErrorTests(unittest.TestCase):
    def test_agent_can_correct_once_after_recoverable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def corrected_context(messages, tools):
                payload = json.loads(messages[1]["content"])
                feedback = payload["recent_tool_results"][-1]
                self.assertEqual("update_working_context", feedback["tool_name"])
                self.assertEqual("invalid_arguments", feedback["result"]["error"]["code"])
                self.assertIn("至少需要两个", feedback["result"]["error"]["message"])
                self.assertEqual([], payload["patient_context"]["known_facts"])
                self.assertEqual([], payload["patient_context"]["conflicts"])
                self.assertEqual(["rec-5"], payload["retrieval_state"]["inspected_record_ids"])
                return call(
                    "corrected",
                    "update_working_context",
                    patient_id="P1",
                    information_need="放弃证据不足的冲突，只保存已读事实。",
                    known_facts=[
                        {
                            "statement": "全文记录包含目标信息。",
                            "evidence_refs": ["rec-5"],
                            "clinical_time": "2026-01-05T00:00:00Z",
                            "temporal_status": "current",
                        }
                    ],
                    unresolved_questions=["另一侧证据是否存在？"],
                    conflicts=[],
                    relevant_record_refs=["rec-5"],
                )

            client = ScriptedClient(
                [
                    call(
                        "read",
                        "get_record",
                        patient_id="P1",
                        information_need="读取一侧全文。",
                        record_id="rec-5",
                    ),
                    invalid_context("bad-context"),
                    corrected_context,
                    final("rec-5"),
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="recover-once"
            )

            self.assertEqual("complete", state.status)
            self.assertEqual([], state.patient_context.conflicts)
            self.assertEqual(1, len(state.patient_context.known_facts))
            calls = read_jsonl(runs / "recover-once" / "tool_calls.jsonl")
            failed = next(item for item in calls if item["call_id"] == "bad-context")
            self.assertEqual("invalid_arguments", failed["output"]["error"]["code"])
            self.assertEqual(
                ["rec-5"],
                state.inspected_record_ids,
                "错误调用不得改变既有 inspected state",
            )
            events = read_jsonl(runs / "recover-once" / "state_events.jsonl")
            recoverable = next(
                event for event in events if event["event_type"] == "recoverable_tool_error"
            )
            self.assertTrue(recoverable["state_unchanged"])
            self.assertEqual(1, recoverable["consecutive_count"])

    def test_consecutive_recoverable_errors_stop_after_small_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [invalid_context("bad-1"), invalid_context("bad-2"), invalid_context("bad-3")]
            )
            runtime, _, runs = make_runtime(
                Path(directory), client, max_consecutive_recoverable_errors=2
            )
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="recover-limit"
            )

            self.assertEqual("stopped", state.status)
            self.assertEqual("recoverable_tool_error_limit", state.stop_reason)
            self.assertEqual([], state.discovered_record_ids)
            self.assertEqual([], state.inspected_record_ids)
            self.assertEqual([], state.patient_context.conflicts)
            calls = read_jsonl(runs / "recover-limit" / "tool_calls.jsonl")
            self.assertEqual(3, len(calls))
            self.assertTrue(all(call_row["output"]["ok"] is False for call_row in calls))

    def test_patient_mismatch_remains_an_immediate_hard_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "wrong-patient",
                        "get_patient",
                        patient_id="P2",
                        information_need="尝试读取另一个患者。",
                    ),
                    final(),
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="patient-hard-stop"
            )

            self.assertEqual("error", state.status)
            self.assertEqual("patient_mismatch", state.stop_reason)
            self.assertEqual(1, len(client.contexts))
            events = read_jsonl(runs / "patient-hard-stop" / "state_events.jsonl")
            self.assertFalse(any(e["event_type"] == "recoverable_tool_error" for e in events))

    def test_unexpected_tool_failure_remains_an_immediate_hard_stop(self) -> None:
        class ExplodingRegistry(ToolRegistry):
            def execute(self, name, arguments, state):
                raise RuntimeError("synthetic internal failure")

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "explode",
                        "get_patient",
                        patient_id="P1",
                        information_need="触发内部失败。",
                    ),
                    final(),
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client)
            runtime.tools = ExplodingRegistry(runtime.tools.database)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="internal-hard-stop"
            )

            self.assertEqual("error", state.status)
            self.assertEqual("tool_failure", state.stop_reason)
            self.assertEqual(1, len(client.contexts))
            calls = read_jsonl(runs / "internal-hard-stop" / "tool_calls.jsonl")
            self.assertEqual("tool_failure", calls[0]["output"]["error"]["code"])


if __name__ == "__main__":
    unittest.main()
