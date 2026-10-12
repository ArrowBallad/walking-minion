from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.models import AgentDecision, ToolCall
from clinical_agent_explorer_phase1.storage import MemoryStore, read_jsonl

from support import ScriptedClient, make_runtime
from test_runtime_flow import call, final


def batch(*calls: ToolCall) -> AgentDecision:
    return AgentDecision(content="按顺序执行这一小批独立调用。", tool_calls=list(calls))


def tool(call_id: str, name: str, **arguments) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


class BatchedToolCallTests(unittest.TestCase):
    def test_oversized_batch_executes_nothing_and_returns_bounded_feedback(self) -> None:
        calls = [
            tool(
                f"read-{index}",
                "get_record",
                patient_id="P1",
                record_id=f"rec-{index}",
                information_need=f"读取第 {index} 条。",
            )
            for index in range(1, 6)
        ]

        def verify_feedback(messages, _tools):
            payload = json.loads(messages[1]["content"])
            feedback = payload["recent_tool_results"][-1]
            self.assertEqual("tool_batch_too_large", feedback["result"]["error"]["code"])
            self.assertEqual(5, len(feedback["result"]["returned_call_ids"]))
            return final()

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([batch(*calls), verify_feedback])
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="batch-too-large"
            )

            self.assertEqual("complete", state.status)
            self.assertEqual([], state.inspected_record_ids)
            self.assertEqual([], read_jsonl(runs / "batch-too-large" / "tool_calls.jsonl"))
            events = read_jsonl(runs / "batch-too-large" / "state_events.jsonl")
            rejected = next(event for event in events if event["event_type"] == "tool_batch_rejected")
            self.assertFalse(rejected["executed"])

    def test_batch_budget_is_checked_before_any_call_executes(self) -> None:
        multi = batch(
            tool(
                "a",
                "get_record",
                patient_id="P1",
                record_id="rec-3",
                information_need="读取第一条。",
            ),
            tool(
                "b",
                "get_record",
                patient_id="P1",
                record_id="rec-5",
                information_need="读取第二条。",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([multi])
            runtime, _, runs = make_runtime(Path(directory), client, max_tool_calls=1)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="batch-budget"
            )

            self.assertEqual("stopped", state.status)
            self.assertEqual("tool_call_limit", state.stop_reason)
            self.assertEqual([], state.inspected_record_ids)
            self.assertEqual([], read_jsonl(runs / "batch-budget" / "tool_calls.jsonl"))

    def test_repeated_oversized_batches_stop_after_bounded_feedback(self) -> None:
        calls = [
            tool(
                f"read-{index}",
                "get_record",
                patient_id="P1",
                record_id="rec-1",
                information_need=f"超大批次调用 {index}。",
            )
            for index in range(5)
        ]
        oversized = batch(*calls)
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([oversized, oversized, oversized])
            runtime, _, runs = make_runtime(
                Path(directory), client, max_consecutive_recoverable_errors=2
            )
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="batch-reject-limit"
            )

            self.assertEqual("stopped", state.status)
            self.assertEqual("recoverable_tool_error_limit", state.stop_reason)
            self.assertEqual([], read_jsonl(runs / "batch-reject-limit" / "tool_calls.jsonl"))
            events = read_jsonl(runs / "batch-reject-limit" / "state_events.jsonl")
            self.assertEqual(
                3,
                len([event for event in events if event["event_type"] == "tool_batch_rejected"]),
            )

    def test_recoverable_error_preserves_prior_success_and_skips_remaining_calls(self) -> None:
        multi = batch(
            tool(
                "read-ok",
                "get_record",
                patient_id="P1",
                record_id="rec-5",
                information_need="先读取合法全文。",
            ),
            tool(
                "bad-context",
                "update_working_context",
                patient_id="P1",
                information_need="提交证据不足的冲突。",
                known_facts=[],
                unresolved_questions=[],
                conflicts=[{"statement": "证据不足。", "evidence_refs": ["rec-5"]}],
                relevant_record_refs=["rec-5"],
            ),
            tool(
                "not-run",
                "get_record",
                patient_id="P1",
                record_id="rec-3",
                information_need="该调用不应执行。",
            ),
        )

        def verify_partial_batch(messages, _tools):
            payload = json.loads(messages[1]["content"])
            results = payload["recent_tool_results"]
            self.assertEqual(
                ["read-ok", "bad-context", "not-run"],
                [item["call_id"] for item in results],
            )
            self.assertTrue(results[0]["result"]["ok"])
            self.assertEqual("invalid_arguments", results[1]["result"]["error"]["code"])
            self.assertTrue(results[2]["result"]["not_executed"])
            self.assertEqual(
                "batch_aborted_after_error", results[2]["result"]["error"]["code"]
            )
            return final("rec-5")

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([multi, verify_partial_batch])
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="partial-batch"
            )

            self.assertEqual("complete", state.status)
            self.assertEqual(["rec-5"], state.inspected_record_ids)
            calls = read_jsonl(runs / "partial-batch" / "tool_calls.jsonl")
            self.assertEqual(["read-ok", "bad-context"], [item["call_id"] for item in calls])
            events = read_jsonl(runs / "partial-batch" / "state_events.jsonl")
            skipped = next(event for event in events if event["event_type"] == "tool_call_not_executed")
            self.assertEqual("not-run", skipped["call_id"])
            interrupted = next(
                event for event in events if event["event_type"] == "tool_batch_interrupted"
            )
            self.assertEqual(["read-ok"], interrupted["successful_call_ids"])
            self.assertTrue(interrupted["partial_side_effects_preserved"])

    def test_batched_writes_each_require_independent_read_back(self) -> None:
        first_batch = batch(
            tool(
                "read",
                "get_record",
                patient_id="P1",
                record_id="rec-5",
                information_need="读取两个模拟写入的共同依据。",
            ),
            tool(
                "create-a",
                "create_order",
                patient_id="P1",
                information_need="创建第一个模拟订单。",
                order_type="模拟订单 A",
                reason="批量写入测试 A",
                evidence_refs=["rec-5"],
            ),
            tool(
                "create-b",
                "create_order",
                patient_id="P1",
                information_need="创建第二个模拟订单。",
                order_type="模拟订单 B",
                reason="批量写入测试 B",
                evidence_refs=["rec-5"],
            ),
        )

        def request_read_backs(messages, _tools):
            payload = json.loads(messages[1]["content"])
            self.assertEqual(2, len(payload["actions"]["proposed"]))
            self.assertEqual([], payload["actions"]["verified"])
            receipts = {
                item["call_id"]: item["result"]["receipt"]["order_id"]
                for item in payload["recent_tool_results"]
                if item["tool_name"] == "create_order"
            }
            return batch(
                tool(
                    "verify-a",
                    "get_order",
                    patient_id="P1",
                    information_need="独立读回第一个订单。",
                    order_id=receipts["create-a"],
                ),
                tool(
                    "verify-b",
                    "get_order",
                    patient_id="P1",
                    information_need="独立读回第二个订单。",
                    order_id=receipts["create-b"],
                ),
            )

        def finish(messages, _tools):
            payload = json.loads(messages[1]["content"])
            order_ids = [item["order_id"] for item in payload["actions"]["verified"]]
            self.assertEqual(2, len(order_ids))
            return final("rec-5", *order_ids)

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([first_batch, request_read_backs, finish])
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅。", case_id="test")

            self.assertEqual("complete", state.status)
            self.assertEqual(2, len(state.proposed_actions))
            self.assertTrue(all(item["status"] == "verified" for item in state.proposed_actions))
            self.assertEqual(2, len(state.verified_actions))

    def test_partial_write_batch_preserves_success_and_does_not_run_later_write(self) -> None:
        write_batch = batch(
            tool(
                "create-a",
                "create_order",
                patient_id="P1",
                information_need="先创建合法订单。",
                order_type="模拟订单 A",
                reason="应保留的写入",
                evidence_refs=["rec-5"],
            ),
            tool(
                "create-b-invalid",
                "create_order",
                patient_id="P1",
                information_need="此订单引用未读证据，应失败。",
                order_type="模拟订单 B",
                reason="非法证据写入",
                evidence_refs=["rec-3"],
            ),
            tool(
                "create-c-skipped",
                "create_order",
                patient_id="P1",
                information_need="前一步失败后不得执行。",
                order_type="模拟订单 C",
                reason="不应执行",
                evidence_refs=["rec-5"],
            ),
        )

        def verify_only_first_order(messages, _tools):
            payload = json.loads(messages[1]["content"])
            self.assertEqual(1, len(payload["actions"]["proposed"]))
            self.assertEqual("unverified", payload["actions"]["proposed"][0]["status"])
            results = payload["recent_tool_results"]
            self.assertEqual(
                ["create-a", "create-b-invalid", "create-c-skipped"],
                [item["call_id"] for item in results],
            )
            self.assertEqual(
                "uninspected_evidence_refs", results[1]["result"]["error"]["code"]
            )
            self.assertTrue(results[2]["result"]["not_executed"])
            return call(
                "verify-a",
                "get_order",
                patient_id="P1",
                information_need="只读回实际成功的第一个订单。",
                order_id=results[0]["result"]["receipt"]["order_id"],
            )

        def finish(messages, _tools):
            payload = json.loads(messages[1]["content"])
            order_id = payload["actions"]["verified"][0]["order_id"]
            return final("rec-5", order_id)

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "read",
                        "get_record",
                        patient_id="P1",
                        record_id="rec-5",
                        information_need="读取合法写入依据。",
                    ),
                    write_batch,
                    verify_only_first_order,
                    finish,
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="partial-write"
            )

            self.assertEqual("complete", state.status)
            self.assertEqual(1, len(state.proposed_actions))
            self.assertEqual(1, len(state.verified_actions))
            orders = read_jsonl(runtime.tools.database.root / "orders.jsonl")
            self.assertEqual(["模拟订单 A"], [item["order_type"] for item in orders])
            calls = read_jsonl(runs / "partial-write" / "tool_calls.jsonl")
            self.assertEqual(
                ["read", "create-a", "create-b-invalid", "verify-a"],
                [item["call_id"] for item in calls],
            )
            events = read_jsonl(runs / "partial-write" / "state_events.jsonl")
            skipped = next(
                event
                for event in events
                if event["event_type"] == "tool_call_not_executed"
            )
            self.assertEqual("create-c-skipped", skipped["call_id"])

    def test_prompt_limit_marks_omitted_batch_results(self) -> None:
        calls = [
            tool(
                f"read-{record_id}",
                "get_record",
                patient_id="P1",
                record_id=record_id,
                information_need=f"读取 {record_id} 的长正文。",
            )
            for record_id in ("rec-1", "rec-2", "rec-3", "rec-4")
        ]

        def verify_prompt_omissions(messages, _tools):
            payload = json.loads(messages[1]["content"])
            omitted_ids = set(
                payload["context_status"]["inspected_without_visible_body"]
            )
            self.assertTrue(omitted_ids)
            self.assertNotIn("full_record_bodies_omitted_from_prompt", payload["context_status"])
            included_ids = {
                item["result"]["record"]["record_id"]
                for item in payload["recent_tool_results"]
                if isinstance(item.get("result"), dict) and item["result"].get("record")
            }
            self.assertTrue(omitted_ids - included_ids)
            return final("rec-1", "rec-2", "rec-3", "rec-4")

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([batch(*calls), verify_prompt_omissions])
            runtime, _, runs = make_runtime(
                Path(directory), client, max_prompt_bytes=18_000
            )
            database = runtime.tools.database.root / "clinical_records.jsonl"
            records = read_jsonl(database)
            for record in records:
                record["data"]["full_text"] = record["data"]["full_text"] + "长正文" * 5000
            database.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )

            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="prompt-batch-limit"
            )
            self.assertEqual("complete", state.status)
            snapshots = read_jsonl(runs / "prompt-batch-limit" / "prompt_snapshots.jsonl")
            self.assertLessEqual(snapshots[-1]["prompt_bytes"], 18_000)
            self.assertTrue(snapshots[-1]["prompt_omitted_tool_results"])
            self.assertTrue(
                all(
                    item.get("call_id")
                    for item in snapshots[-1]["prompt_omitted_tool_results"]
                )
            )
            self.assertTrue(snapshots[-1]["prompt_budget"]["trims"])


class FinalMemoryDecouplingTests(unittest.TestCase):
    def test_memory_updates_field_may_be_omitted(self) -> None:
        without_memory = AgentDecision(
            content=json.dumps(
                {
                    "final_text": "不需要附属记忆的合法主回答。",
                    "run_summary": "直接完成。",
                    "evidence_refs": [],
                },
                ensure_ascii=False,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([without_memory])
            runtime, memory_path, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P1", task="审阅。", case_id="test")

            self.assertEqual("complete", state.status)
            self.assertEqual([], MemoryStore(memory_path).for_patient("P1"))

    def test_invalid_memory_update_is_skipped_while_valid_final_completes(self) -> None:
        def mixed_final(_messages, _tools):
            return AgentDecision(
                content=json.dumps(
                    {
                        "final_text": "主回答只引用已读全文。",
                        "run_summary": "读取一条记录后完成。",
                        "evidence_refs": ["rec-5"],
                        "memory_updates": [
                            {
                                "kind": "prior_work",
                                "text": "合法记忆。",
                                "evidence_refs": ["rec-5"],
                                "status": "active",
                            },
                            {
                                "kind": "unresolved",
                                "text": "非法记忆引用未读记录。",
                                "evidence_refs": ["rec-3"],
                                "status": "active",
                            },
                        ],
                    },
                    ensure_ascii=False,
                )
            )

        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    call(
                        "read",
                        "get_record",
                        patient_id="P1",
                        record_id="rec-5",
                        information_need="读取主 final 的依据。",
                    ),
                    mixed_final,
                ]
            )
            runtime, memory_path, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅。", case_id="test", run_id="memory-decoupled"
            )

            self.assertEqual("complete", state.status)
            memories = MemoryStore(memory_path).for_patient("P1")
            self.assertEqual(["合法记忆。"], [item["text"] for item in memories])
            events = read_jsonl(runs / "memory-decoupled" / "state_events.jsonl")
            rejected = [event for event in events if event["event_type"] == "memory_update_rejected"]
            self.assertEqual(1, len(rejected))
            self.assertEqual("invalid_final_evidence", rejected[0]["reason"])
            final_event = next(event for event in events if event["event_type"] == "final")
            self.assertEqual(1, final_event["memory_updates_written"])
            self.assertEqual(1, final_event["memory_updates_rejected"])


if __name__ == "__main__":
    unittest.main()
