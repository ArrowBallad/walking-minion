from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.models import AgentDecision, ToolCall
from clinical_agent_explorer_phase1.runtime import ClinicalAgentRuntime
from clinical_agent_explorer_phase1.storage import MemoryStore, MockDatabase, read_jsonl
from clinical_agent_explorer_phase1.tools import ToolRegistry

from support import ScriptedClient, make_runtime


def call(call_id: str, name: str, **arguments) -> AgentDecision:
    return AgentDecision(
        content="执行一个有明确缺口的查询。",
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
    )


def final(*refs: str) -> AgentDecision:
    return AgentDecision(
        content=json.dumps(
            {
                "final_text": "基于已读取全文记录形成的合成评估。",
                "run_summary": "先检索索引，再按需读取全文并更新 working context。",
                "evidence_refs": list(refs),
                "memory_updates": [],
            },
            ensure_ascii=False,
        )
    )


class RuntimeFlowTests(unittest.TestCase):
    def test_index_to_full_record_to_working_context_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "search",
                        "search_records",
                        patient_id="P1",
                        information_need="寻找近期相关记录索引。",
                        keyword="目标",
                        limit=10,
                    ),
                    call(
                        "read",
                        "get_record",
                        patient_id="P1",
                        information_need="读取命中记录全文。",
                        record_id="rec-5",
                    ),
                    call(
                        "context",
                        "update_working_context",
                        patient_id="P1",
                        information_need="保存已确认事实并说明剩余缺口。",
                        known_facts=[
                            {
                                "statement": "全文记录包含目标信息。",
                                "evidence_refs": ["rec-5"],
                                "clinical_time": "2026-01-05T00:00:00Z",
                                "temporal_status": "current",
                            }
                        ],
                        unresolved_questions=["是否还有其他相关记录？"],
                        conflicts=[],
                        relevant_record_refs=["rec-5"],
                    ),
                    final("rec-5"),
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅目标问题。", case_id="test", run_id="flow"
            )

            self.assertEqual("complete", state.status)
            self.assertIn("rec-5", state.discovered_record_ids)
            self.assertEqual(["rec-5"], state.inspected_record_ids)
            self.assertEqual("全文记录包含目标信息。", state.patient_context.known_facts[0]["statement"])

            calls = read_jsonl(runs / "flow" / "tool_calls.jsonl")
            self.assertNotIn("data", calls[0]["output"]["records"][0])
            self.assertIn("data", calls[1]["output"]["record"])

    def test_prompt_uses_working_context_and_only_recent_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "patient",
                        "get_patient",
                        patient_id="P1",
                        information_need="确认患者身份。",
                    ),
                    call(
                        "search",
                        "search_records",
                        patient_id="P1",
                        information_need="寻找记录。",
                        limit=2,
                    ),
                    call(
                        "read",
                        "get_record",
                        patient_id="P1",
                        information_need="读取全文。",
                        record_id="rec-6",
                    ),
                    final("patient-P1", "rec-6"),
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client, recent_result_limit=2)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="bounded"
            )
            self.assertEqual("complete", state.status)

            snapshots = read_jsonl(runs / "bounded" / "prompt_snapshots.jsonl")
            last_payload = json.loads(snapshots[-1]["messages"][1]["content"])
            self.assertEqual(2, len(last_payload["recent_tool_results"]))
            self.assertEqual(1, last_payload["omitted_prior_tool_result_count"])
            self.assertEqual(["rec-6"], last_payload["retrieval_state"]["inspected_record_ids"])
            self.assertEqual(3, len(last_payload["recent_tool_activity"]))
            self.assertEqual("get_patient", last_payload["recent_tool_activity"][0]["tool_name"])
            self.assertNotIn("patient", last_payload["recent_tool_activity"][0])
            self.assertEqual("rec-6", last_payload["recent_tool_activity"][-1]["record_id"])

    def test_first_prompt_contains_no_case_record_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([final()])
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅。", case_id="test")
            self.assertEqual("complete", state.status)
            first_prompt = json.dumps(client.contexts[0], ensure_ascii=False)
            self.assertNotIn("仅全文可见的内容", first_prompt)
            self.assertNotIn("rec-1", first_prompt)

    def test_memory_reference_does_not_count_as_inspected_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = ScriptedClient([final("rec-3"), final("rec-3"), final("rec-3")])
            runtime, memory_path, _ = make_runtime(root, client)
            MemoryStore(memory_path).append(
                patient_id="P1",
                source_run_id="prior",
                kind="prior_work",
                text="之前看过 rec-3。",
                evidence_refs=["rec-3"],
            )
            state = runtime.run(patient_id="P1", task="再次审阅。", case_id="test")
            self.assertEqual("error", state.status)
            self.assertEqual("invalid_final_evidence", state.stop_reason)
            self.assertEqual([], state.inspected_record_ids)

    def test_invalid_final_can_be_revised_without_relaxing_evidence_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid = AgentDecision(
                content=json.dumps(
                    {
                        "final_text": "错误地引用了未读取记录。",
                        "run_summary": "先提交一份无效结果。",
                        "evidence_refs": ["rec-3"],
                        "memory_updates": [],
                    },
                    ensure_ascii=False,
                )
            )
            client = ScriptedClient([invalid, final()])
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="final-revision"
            )

            self.assertEqual("complete", state.status)
            second_payload = json.loads(client.contexts[1][1]["content"])
            feedback = second_payload["recent_tool_results"][-1]
            self.assertEqual("runtime_validation", feedback["tool_name"])
            self.assertEqual("invalid_final_evidence", feedback["result"]["error"]["code"])
            events = read_jsonl(runs / "final-revision" / "state_events.jsonl")
            self.assertTrue(any(event["event_type"] == "final_rejected" for event in events))

    def test_multiple_tool_calls_are_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            multi = AgentDecision(
                content="尝试批量读取。",
                tool_calls=[
                    ToolCall(
                        call_id="a",
                        name="get_record",
                        arguments={
                            "patient_id": "P1",
                            "record_id": "rec-3",
                            "information_need": "读取第一条。",
                        },
                    ),
                    ToolCall(
                        call_id="b",
                        name="get_record",
                        arguments={
                            "patient_id": "P1",
                            "record_id": "rec-5",
                            "information_need": "读取第二条。",
                        },
                    ),
                ],
            )
            client = ScriptedClient(
                [
                    multi,
                    call(
                        "single",
                        "get_record",
                        patient_id="P1",
                        record_id="rec-5",
                        information_need="按原子契约只读取一条。",
                    ),
                    final("rec-5"),
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="atomic"
            )

            self.assertEqual("complete", state.status)
            self.assertEqual(["rec-5"], state.inspected_record_ids)
            calls = read_jsonl(runs / "atomic" / "tool_calls.jsonl")
            self.assertEqual(["single"], [item["call_id"] for item in calls])
            retry_payload = json.loads(client.contexts[1][1]["content"])
            self.assertEqual(
                "multiple_tool_calls_not_allowed",
                retry_payload["recent_tool_results"][-1]["result"]["error"]["code"],
            )

    def test_receipt_requires_matching_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def read_order(messages, tools):
                payload = json.loads(messages[1]["content"])
                recent = payload["recent_tool_results"][-1]["result"]
                return call(
                    "verify",
                    "get_order",
                    patient_id="P1",
                    information_need="独立读回刚创建的订单。",
                    order_id=recent["receipt"]["order_id"],
                )

            def final_order(messages, tools):
                payload = json.loads(messages[1]["content"])
                order_id = payload["actions"]["verified"][0]["order_id"]
                return final("rec-5", order_id)

            client = ScriptedClient(
                [
                    call(
                        "read",
                        "get_record",
                        patient_id="P1",
                        information_need="读取写入依据。",
                        record_id="rec-5",
                    ),
                    call(
                        "create",
                        "create_order",
                        patient_id="P1",
                        information_need="根据已读全文创建模拟随访。",
                        order_type="模拟随访",
                        reason="测试 read-back 边界",
                        evidence_refs=["rec-5"],
                    ),
                    read_order,
                    final_order,
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅。", case_id="test")
            self.assertEqual("complete", state.status)
            self.assertEqual("verified", state.proposed_actions[0]["status"])
            self.assertEqual(1, len(state.verified_actions))


if __name__ == "__main__":
    unittest.main()
