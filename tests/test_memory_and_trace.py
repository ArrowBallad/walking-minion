from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer.models import AgentDecision, ToolCall
from clinical_agent_explorer.storage import MemoryStore, MockDatabase, read_jsonl
from clinical_agent_explorer.runtime import ClinicalAgentRuntime
from clinical_agent_explorer.tools import ToolRegistry

from support import ScriptedClient, final_decision, make_runtime


class MemoryTests(unittest.TestCase):
    def test_memory_is_loaded_as_prior_work_but_not_current_run_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_client = ScriptedClient(
                [
                    AgentDecision(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                call_id="search",
                                name="search_records",
                                arguments={"patient_id": "P-001"},
                            )
                        ],
                    ),
                    final_decision(
                        evidence_refs=["rec-old", "rec-new"],
                        memory_updates=[
                            {
                                "kind": "prior_work",
                                "text": "A scripted prior-run observation.",
                                "evidence_refs": ["rec-old"],
                                "status": "active",
                            }
                        ],
                    ),
                ]
            )
            runtime, memory_path, runs = make_runtime(root, first_client)
            first = runtime.run(
                patient_id="P-001", task="Review.", case_id="test", run_id="first"
            )
            self.assertEqual("complete", first.status)
            self.assertEqual(1, len(read_jsonl(memory_path)))

            second_client = ScriptedClient([final_decision(evidence_refs=["rec-old"])])
            database = root / "mock_db"
            second_runtime = ClinicalAgentRuntime(
                client=second_client,
                tools=ToolRegistry(MockDatabase(database)),
                memory=MemoryStore(memory_path),
                runs_root=runs,
            )
            second = second_runtime.run(
                patient_id="P-001", task="Review again.", case_id="test", run_id="second"
            )

            self.assertEqual("error", second.status)
            self.assertEqual("invalid_final_evidence", second.stop_reason)
            first_context = json.dumps(second_client.contexts[0], ensure_ascii=False, sort_keys=True)
            self.assertIn("A scripted prior-run observation.", first_context)
            self.assertIn("仅代表之前的工作", first_context)
            self.assertEqual([], second.retrieved_record_ids)

    def test_memory_can_be_used_after_a_fresh_database_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, memory_path, runs = make_runtime(root, ScriptedClient([final_decision()]))
            memory = MemoryStore(memory_path)
            memory.append(
                patient_id="P-001",
                source_run_id="older-run",
                kind="unresolved",
                text="Prior work to revisit.",
                evidence_refs=["rec-old"],
            )
            client = ScriptedClient(
                [
                    AgentDecision(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                call_id="fresh-search",
                                name="search_records",
                                arguments={"patient_id": "P-001"},
                            )
                        ],
                    ),
                    final_decision(evidence_refs=["rec-old"]),
                ]
            )
            runtime = ClinicalAgentRuntime(
                client=client,
                tools=runtime.tools,
                memory=memory,
                runs_root=runs,
            )
            state = runtime.run(
                patient_id="P-001", task="Review again.", case_id="test", run_id="fresh-read"
            )
            self.assertEqual("complete", state.status)
            self.assertIn("rec-old", state.retrieved_record_ids)


if __name__ == "__main__":
    unittest.main()
