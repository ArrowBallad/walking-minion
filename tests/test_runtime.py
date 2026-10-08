from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer.models import AgentDecision, ToolCall
from clinical_agent_explorer.storage import read_jsonl

from support import RepeatingClient, ScriptedClient, final_decision, make_runtime


def search_call(call_id: str = "call-search") -> AgentDecision:
    return AgentDecision(
        content=None,
        tool_calls=[
            ToolCall(
                call_id=call_id,
                name="search_records",
                arguments={"patient_id": "P-001", "record_type": "note"},
            )
        ],
    )


class RuntimeTests(unittest.TestCase):
    def test_multiple_tool_calls_are_dispatched_sequentially_and_traced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    AgentDecision(
                        content="并行提出两个独立读取请求。",
                        tool_calls=[
                            ToolCall(
                                call_id="patient",
                                name="get_patient",
                                arguments={"patient_id": "P-001"},
                            ),
                            ToolCall(
                                call_id="records",
                                name="search_records",
                                arguments={"patient_id": "P-001"},
                            ),
                        ],
                    ),
                    final_decision(evidence_refs=["patient-P-001", "rec-old", "rec-new"]),
                ]
            )
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P-001", task="审阅。", case_id="test", run_id="multi-tool"
            )

            self.assertEqual("complete", state.status)
            self.assertEqual(
                ["patient-P-001", "rec-old", "rec-new"], state.retrieved_record_ids
            )
            calls = read_jsonl(runs / state.run_id / "tool_calls.jsonl")
            self.assertEqual(["get_patient", "search_records"], [row["tool_name"] for row in calls])

    def test_first_prompt_contains_no_patient_records_or_filesystem_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([final_decision()])
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P-001", task="Review.", case_id="test")

            self.assertEqual("complete", state.status)
            first_context = json.dumps(client.contexts[0], sort_keys=True)
            self.assertNotIn("rec-old", first_context)
            self.assertNotIn("older", first_context)
            tool_names = [item["function"]["name"] for item in client.tool_sets[0]]
            self.assertEqual(
                ["get_patient", "search_records", "create_order", "get_order"], tool_names
            )

    def test_receipt_stays_unverified_without_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient(
                [
                    search_call(),
                    AgentDecision(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                call_id="call-create",
                                name="create_order",
                                arguments={
                                    "patient_id": "P-001",
                                    "order_type": "synthetic-follow-up",
                                    "reason": "scripted transaction test",
                                    "evidence_refs": ["rec-new"],
                                },
                            )
                        ],
                    ),
                    final_decision(evidence_refs=["rec-new"]),
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P-001", task="Review.", case_id="test")

            self.assertEqual("complete", state.status)
            self.assertEqual("unverified", state.proposed_actions[0]["status"])
            self.assertEqual([], state.verified_actions)

    def test_matching_read_back_is_the_only_verification_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def read_created_order(messages, tools):
                tool_messages = [message for message in messages if message["role"] == "tool"]
                create_output = json.loads(tool_messages[-1]["content"])
                return AgentDecision(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            call_id="call-read-back",
                            name="get_order",
                            arguments={
                                "patient_id": "P-001",
                                "order_id": create_output["receipt"]["order_id"],
                            },
                        )
                    ],
                )

            def final_with_verified_action(messages, tools):
                tool_messages = [message for message in messages if message["role"] == "tool"]
                read_back = json.loads(tool_messages[-1]["content"])
                order_id = read_back["order"]["order_id"]
                return final_decision(
                    evidence_refs=["rec-new", order_id],
                    memory_updates=[
                        {
                            "kind": "next_step",
                            "text": "已验证的合成 action。",
                            "evidence_refs": ["rec-new", order_id],
                            "status": "active",
                        }
                    ],
                )

            client = ScriptedClient(
                [
                    search_call(),
                    AgentDecision(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                call_id="call-create",
                                name="create_order",
                                arguments={
                                    "patient_id": "P-001",
                                    "order_type": "synthetic-follow-up",
                                    "reason": "scripted transaction test",
                                    "evidence_refs": ["rec-new"],
                                },
                            )
                        ],
                    ),
                    read_created_order,
                    final_with_verified_action,
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(patient_id="P-001", task="Review.", case_id="test")

            self.assertEqual("verified", state.proposed_actions[0]["status"])
            self.assertEqual(1, len(state.verified_actions))

    def test_mismatched_read_back_never_becomes_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def read_created_order(messages, tools):
                tool_messages = [message for message in messages if message["role"] == "tool"]
                create_output = json.loads(tool_messages[-1]["content"])
                return AgentDecision(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            call_id="call-read-back",
                            name="get_order",
                            arguments={
                                "patient_id": "P-001",
                                "order_id": create_output["receipt"]["order_id"],
                            },
                        )
                    ],
                )

            client = ScriptedClient(
                [
                    search_call(),
                    AgentDecision(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                call_id="call-create",
                                name="create_order",
                                arguments={
                                    "patient_id": "P-001",
                                    "order_type": "synthetic-follow-up",
                                    "reason": "scripted transaction test",
                                    "evidence_refs": ["rec-new"],
                                },
                            )
                        ],
                    ),
                    read_created_order,
                    final_decision(evidence_refs=["rec-new"]),
                ]
            )
            runtime, _, _ = make_runtime(Path(directory), client)
            execute = runtime.tools.execute

            def mismatching_read_back(name, arguments, state):
                result = execute(name, arguments, state)
                if name == "get_order" and result.get("order"):
                    result["order"] = dict(result["order"])
                    result["order"]["reason"] = "different stored value"
                return result

            runtime.tools.execute = mismatching_read_back  # type: ignore[method-assign]
            state = runtime.run(patient_id="P-001", task="Review.", case_id="test")

            self.assertEqual("verification_failed", state.proposed_actions[0]["status"])
            self.assertEqual([], state.verified_actions)

    def test_invalid_arguments_and_iteration_limit_stop_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid = ScriptedClient(
                [
                    AgentDecision(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                call_id="bad",
                                name="search_records",
                                arguments={"patient_id": "P-001", "unexpected": "value"},
                            )
                        ],
                    )
                ]
            )
            runtime, _, _ = make_runtime(Path(directory) / "invalid", invalid)
            invalid_state = runtime.run(
                patient_id="P-001", task="Review.", case_id="test", run_id="invalid"
            )
            self.assertEqual("error", invalid_state.status)
            self.assertEqual("invalid_arguments", invalid_state.stop_reason)

            repeating = RepeatingClient(search_call())
            runtime, _, _ = make_runtime(Path(directory) / "limit", repeating, max_steps=2)
            limited_state = runtime.run(
                patient_id="P-001", task="Review.", case_id="test", run_id="limited"
            )
            self.assertEqual("stopped", limited_state.status)
            self.assertEqual("iteration_limit", limited_state.stop_reason)

    def test_unexpected_tool_failure_is_recorded_and_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([search_call()])
            runtime, _, runs = make_runtime(Path(directory), client)

            def fail_tool(name, arguments, state):
                raise OSError("synthetic storage failure")

            runtime.tools.execute = fail_tool  # type: ignore[method-assign]
            state = runtime.run(
                patient_id="P-001", task="Review.", case_id="test", run_id="tool-failure"
            )

            self.assertEqual("error", state.status)
            self.assertEqual("tool_failure", state.stop_reason)
            calls = read_jsonl(runs / state.run_id / "tool_calls.jsonl")
            self.assertEqual("tool_failure", calls[0]["output"]["error"]["code"])
            self.assertIn("synthetic storage failure", calls[0]["output"]["error"]["message"])

    def test_trace_contains_prompts_raw_tools_state_changes_and_final(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([search_call(), final_decision(evidence_refs=["rec-old", "rec-new"])])
            runtime, _, runs = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P-001", task="Review.", case_id="test", run_id="trace-run"
            )
            run_root = runs / state.run_id
            events = read_jsonl(run_root / "state_events.jsonl")
            model_calls = read_jsonl(run_root / "model_calls.jsonl")
            calls = read_jsonl(run_root / "tool_calls.jsonl")
            prompts = read_jsonl(run_root / "prompt_snapshots.jsonl")

            self.assertEqual([1, 2], [row["step"] for row in prompts])
            self.assertEqual([1, 2], [row["step"] for row in model_calls])
            self.assertEqual("rec-old", calls[0]["output"]["records"][0]["record_id"])
            event_types = [row["event_type"] for row in events]
            self.assertIn("prompt", event_types)
            self.assertIn("model_response", event_types)
            self.assertIn("tool_call", event_types)
            self.assertIn("tool_result", event_types)
            self.assertIn("state_change", event_types)
            self.assertEqual("final", event_types[-1])
            self.assertTrue((run_root / "final.md").is_file())


if __name__ == "__main__":
    unittest.main()
