from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.models import (
    AgentDecision,
    PatientContext,
    RunState,
    ToolCall,
)
from clinical_agent_explorer_phase1.runtime import ClinicalAgentRuntime
from clinical_agent_explorer_phase1.storage import MemoryStore, MockDatabase
from clinical_agent_explorer_phase1.tools import ToolError, ToolRegistry

from support import ScriptedClient


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "cases" / "gastro_demo" / "gi_demo_001"
PATIENT_ID = "GI-DEMO-001"
AS_OF = {
    "E01": "2025-01-24T23:59:59Z",
    "E02": "2025-03-07T23:59:59Z",
    "E03": "2025-09-20T23:59:59Z",
}


def execute(
    tools: ToolRegistry, state: RunState, name: str, **arguments: object
) -> dict:
    return tools.execute(
        name,
        {
            "patient_id": PATIENT_ID,
            "information_need": "确定性 encounter 测试。",
            **arguments,
        },
        state,
    )


class EncounterTemporalIsolationTests(unittest.TestCase):
    def _tools(self, encounter: str) -> tuple[ToolRegistry, RunState]:
        return (
            ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF[encounter])),
            RunState(
                run_id=f"test-{encounter}",
                patient_id=PATIENT_ID,
                task="通用 encounter 审阅。",
                encounter_id=encounter,
                as_of=AS_OF[encounter],
            ),
        )

    def test_cumulative_availability_and_stable_ids(self) -> None:
        expected = {
            "E01": [f"gi-demo-001-rec-{index:03d}" for index in range(1, 6)],
            "E02": [f"gi-demo-001-rec-{index:03d}" for index in range(1, 14)],
            "E03": [f"gi-demo-001-rec-{index:03d}" for index in range(1, 19)],
        }
        for encounter, expected_ids in expected.items():
            tools, state = self._tools(encounter)
            result = execute(tools, state, "search_records", limit=20)
            actual = sorted(row["record_id"] for row in result["records"])
            self.assertEqual(expected_ids, actual)
            self.assertTrue(all("available_at" in row for row in result["records"]))

    def test_direct_get_record_cannot_bypass_boundary(self) -> None:
        e01_tools, e01_state = self._tools("E01")
        e02_tools, e02_state = self._tools("E02")
        self.assertFalse(
            execute(
                e01_tools,
                e01_state,
                "get_record",
                record_id="gi-demo-001-rec-006",
            )["found"]
        )
        self.assertFalse(
            execute(
                e02_tools,
                e02_state,
                "get_record",
                record_id="gi-demo-001-rec-014",
            )["found"]
        )
        self.assertTrue(
            execute(
                e02_tools,
                e02_state,
                "get_record",
                record_id="gi-demo-001-rec-013",
            )["found"]
        )

    def test_index_does_not_leak_later_or_result_level_findings(self) -> None:
        for encounter, forbidden_ids in {
            "E01": range(6, 19),
            "E02": range(14, 19),
        }.items():
            tools, state = self._tools(encounter)
            result = execute(tools, state, "search_records", limit=20)
            serialized = json.dumps(result, ensure_ascii=False)
            for index in forbidden_ids:
                self.assertNotIn(f"gi-demo-001-rec-{index:03d}", serialized)
        tools, state = self._tools("E03")
        index_text = json.dumps(
            execute(tools, state, "search_records", limit=20), ensure_ascii=False
        )
        for result_term in ("Marsh", "绒毛萎缩", "上皮内淋巴细胞", "症状改善"):
            self.assertNotIn(result_term, index_text)

    def test_available_workflow_references_remain_stable(self) -> None:
        tools, state = self._tools("E02")
        request = execute(
            tools, state, "get_record", record_id="gi-demo-001-rec-009"
        )["record"]
        procedure = execute(
            tools, state, "get_record", record_id="gi-demo-001-rec-010"
        )["record"]
        self.assertEqual("gi-demo-001-rec-010", request["data"]["procedure_reference"])
        self.assertEqual("gi-demo-001-rec-009", procedure["data"]["request_reference"])
        self.assertEqual("upper-endoscopy-2025-02", request["workflow_id"])

    def test_first_prompt_has_no_record_or_reference_content(self) -> None:
        all_record_ids = [f"gi-demo-001-rec-{index:03d}" for index in range(1, 19)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = MemoryStore(root / "memory.jsonl")
            for encounter in AS_OF:
                tools, state = self._tools(encounter)
                runtime = ClinicalAgentRuntime(
                    client=object(),
                    tools=tools,
                    memory=memory,
                    runs_root=root / "runs",
                    max_steps=10,
                    max_tool_calls=12,
                )
                state.step = 1
                messages, _ = runtime._build_prompt(
                    state=state,
                    task=state.task,
                    memory=memory.effective_for_patient(PATIENT_ID),
                    recent_results=[],
                    recent_activity=[],
                    omitted_results=0,
                )
                prompt = json.dumps(messages, ensure_ascii=False)
                self.assertTrue(all(record_id not in prompt for record_id in all_record_ids))
                self.assertNotIn("gastro_demo_reference", prompt)
                self.assertNotIn("Underlying case truth", prompt)


class IncrementalContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF["E03"]))
        self.state = RunState(run_id="context", patient_id=PATIENT_ID, task="test")
        self.state.inspected_record_ids = [
            "gi-demo-001-rec-001",
            "gi-demo-001-rec-002",
            "gi-demo-001-rec-003",
        ]

    def _update(self, operations: list[dict]) -> dict:
        result = execute(
            self.tools,
            self.state,
            "update_working_context",
            operations=operations,
        )
        self.state.patient_context = PatientContext(**result["patient_context"])
        return result

    def test_incremental_add_update_supersede_and_resolution(self) -> None:
        self._update(
            [
                {
                    "op": "add_finding",
                    "entry_id": "finding-baseline",
                    "statement": "基线记录已读取。",
                    "evidence_refs": ["gi-demo-001-rec-001"],
                    "clinical_time": "2024-06-12T07:45:00Z",
                    "temporal_status": "historical",
                },
                {
                    "op": "add_question",
                    "entry_id": "question-change",
                    "text": "后续是否出现变化？",
                    "evidence_refs": ["gi-demo-001-rec-001"],
                },
            ]
        )
        self._update(
            [
                {
                    "op": "add_finding",
                    "entry_id": "finding-symptom",
                    "statement": "出现新症状。",
                    "evidence_refs": ["gi-demo-001-rec-002"],
                    "clinical_time": "2025-01-22T09:00:00Z",
                    "temporal_status": "current",
                },
                {
                    "op": "resolve_question",
                    "entry_id": "question-change",
                    "resolution": "已观察到变化。",
                    "evidence_refs": ["gi-demo-001-rec-002"],
                },
            ]
        )
        self.assertEqual(2, len(self.state.patient_context.known_facts))
        self.assertEqual([], self.state.patient_context.unresolved_questions)
        self.assertEqual(
            "resolved", self.state.patient_context.question_states[0]["status"]
        )
        self._update(
            [
                {
                    "op": "update_finding",
                    "entry_id": "finding-symptom",
                    "statement": "新症状伴有实验室异常。",
                    "evidence_refs": [
                        "gi-demo-001-rec-002",
                        "gi-demo-001-rec-003",
                    ],
                    "clinical_time": "2025-01-22T10:20:00Z",
                    "temporal_status": "current",
                },
                {
                    "op": "supersede_finding",
                    "entry_id": "finding-baseline",
                    "replacement_entry_id": "finding-later",
                    "statement": "后续状态取代基线。",
                    "evidence_refs": ["gi-demo-001-rec-003"],
                    "clinical_time": "2025-01-22T10:20:00Z",
                    "temporal_status": "current",
                },
            ]
        )
        by_id = {
            item["entry_id"]: item for item in self.state.patient_context.known_facts
        }
        self.assertEqual("superseded", by_id["finding-baseline"]["temporal_status"])
        self.assertEqual("finding-baseline", by_id["finding-later"]["supersedes"])
        self.assertIn("gi-demo-001-rec-002", by_id["finding-symptom"]["evidence_refs"])

    def test_unknown_or_uninspected_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ToolError, "未读取全文"):
            self._update(
                [
                    {
                        "op": "add_finding",
                        "entry_id": "future",
                        "statement": "不允许的事实。",
                        "evidence_refs": ["gi-demo-001-rec-018"],
                        "clinical_time": None,
                        "temporal_status": "unknown",
                    }
                ]
            )
        with self.assertRaisesRegex(ToolError, "未知 finding"):
            self._update(
                [
                    {
                        "op": "update_finding",
                        "entry_id": "missing",
                        "statement": "未知条目。",
                        "evidence_refs": ["gi-demo-001-rec-001"],
                        "clinical_time": None,
                        "temporal_status": "unknown",
                    }
                ]
            )

    def test_mixed_proposal_is_atomic_and_reports_bad_operation(self) -> None:
        operations = [
            {
                "op": "add_finding",
                "entry_id": f"finding-{index}",
                "statement": f"已读取事实 {index}。",
                "evidence_refs": [f"gi-demo-001-rec-00{index}"],
                "clinical_time": None,
                "temporal_status": "current",
            }
            for index in range(1, 4)
        ] + [
            {
                "op": "add_finding",
                "entry_id": "finding-4",
                "statement": "已读取事实 4。",
                "evidence_refs": ["gi-demo-001-rec-001"],
                "clinical_time": None,
                "temporal_status": "current",
            },
            {
                "op": "resolve_question",
                "entry_id": "q-followup-adherence-efficacy",
                "resolution": "尝试解决不存在的问题。",
                "evidence_refs": ["gi-demo-001-rec-001"],
            },
        ]
        before = self.state.to_dict()
        with self.assertRaises(ToolError) as caught:
            self._update(operations)

        error = caught.exception.as_dict()["error"]
        self.assertEqual("unknown_working_question_id", error["code"])
        self.assertEqual(4, error["operation_index"])
        self.assertEqual("q-followup-adherence-efficacy", error["target_id"])
        self.assertEqual([], error["valid_question_ids"])
        self.assertFalse(error["applied"])
        self.assertEqual(4, error["validated_prior_operation_count"])
        self.assertEqual(before, self.state.to_dict())

    def test_memory_id_is_rejected_with_explicit_scope_guidance(self) -> None:
        self.state.loaded_memory_ids = ["mem-followup-question"]
        with self.assertRaises(ToolError) as caught:
            self._update(
                [
                    {
                        "op": "resolve_question",
                        "entry_id": "mem-followup-question",
                        "resolution": "错误地把 memory 当成本轮问题。",
                        "evidence_refs": ["gi-demo-001-rec-001"],
                    }
                ]
            )
        error = caught.exception.as_dict()["error"]
        self.assertEqual("id_scope_mismatch", error["code"])
        self.assertEqual(0, error["operation_index"])
        self.assertEqual("mem-followup-question", error["target_id"])
        self.assertIn("final.memory_updates", error["scope_guidance"])
        self.assertFalse(error["applied"])

    def test_structured_retry_cannot_bypass_uninspected_evidence(self) -> None:
        with self.assertRaises(ToolError) as caught:
            self._update(
                [
                    {
                        "op": "add_finding",
                        "entry_id": "future",
                        "statement": "未读证据不能借 retry 写入。",
                        "evidence_refs": ["gi-demo-001-rec-018"],
                        "clinical_time": None,
                        "temporal_status": "unknown",
                    }
                ]
            )
        error = caught.exception.as_dict()["error"]
        self.assertEqual("uninspected_context_ref", error["code"])
        self.assertEqual(0, error["operation_index"])
        self.assertFalse(error["applied"])


class WorkingContextRetryRuntimeTests(unittest.TestCase):
    def test_corrected_findings_apply_without_rereading_records(self) -> None:
        record_ids = [
            "gi-demo-001-rec-014",
            "gi-demo-001-rec-015",
            "gi-demo-001-rec-017",
            "gi-demo-001-rec-018",
        ]
        finding_operations = [
            {
                "op": "add_finding",
                "entry_id": f"followup-finding-{index}",
                "statement": f"随访事实 {index} 已从全文读取。",
                "evidence_refs": [record_id],
                "clinical_time": None,
                "temporal_status": "current",
            }
            for index, record_id in enumerate(record_ids, 1)
        ]
        invalid_operations = finding_operations + [
            {
                "op": "resolve_question",
                "entry_id": "q-followup-adherence-efficacy",
                "resolution": "错误目标。",
                "evidence_refs": ["gi-demo-001-rec-017"],
            }
        ]

        read_batch = AgentDecision(
            content="读取四条随访证据。",
            tool_calls=[
                ToolCall(
                    call_id=f"read-{index}",
                    name="get_record",
                    arguments={
                        "patient_id": PATIENT_ID,
                        "information_need": "读取随访全文。",
                        "record_id": record_id,
                    },
                )
                for index, record_id in enumerate(record_ids, 1)
            ],
        )
        invalid_update = AgentDecision(
            content="保存 findings 并错误地解决一个非本轮问题。",
            tool_calls=[
                ToolCall(
                    call_id="invalid-context",
                    name="update_working_context",
                    arguments={
                        "patient_id": PATIENT_ID,
                        "information_need": "保存随访事实。",
                        "operations": invalid_operations,
                    },
                )
            ],
        )

        def corrected_update(messages, schemas):
            payload = json.loads(messages[1]["content"])
            retry = payload["working_context_retry"]
            self.assertEqual("unapplied_proposal", retry["status"])
            self.assertEqual(
                "unknown_working_question_id", retry["error"]["code"]
            )
            self.assertEqual(4, retry["error"]["operation_index"])
            self.assertEqual(5, len(retry["proposed_arguments"]["operations"]))
            self.assertEqual([], payload["id_scopes"]["working_context_active_question_ids"])
            self.assertEqual([], payload["patient_context"]["known_facts"])
            prompt_bytes = len(
                json.dumps(
                    {"messages": messages, "tools": schemas},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            )
            self.assertLessEqual(prompt_bytes, 32_000)
            return AgentDecision(
                content="删除无效 resolve，仅重新提交已提出的 findings。",
                tool_calls=[
                    ToolCall(
                        call_id="corrected-context",
                        name="update_working_context",
                        arguments={
                            "patient_id": PATIENT_ID,
                            "information_need": "修正结构，不重新读取病历。",
                            "operations": finding_operations,
                        },
                    )
                ],
            )

        final = AgentDecision(
            content=json.dumps(
                {
                    "final_text": "已保存四条随访发现。",
                    "run_summary": "失败提案经结构化反馈修正，无重复读取。",
                    "evidence_refs": record_ids,
                    "memory_updates": [],
                },
                ensure_ascii=False,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = ScriptedClient(
                [read_batch, invalid_update, corrected_update, final]
            )
            runtime = ClinicalAgentRuntime(
                client=client,
                tools=ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF["E03"])),
                memory=MemoryStore(root / "memory.jsonl"),
                runs_root=root / "runs",
                max_steps=10,
                max_tool_calls=12,
                max_prompt_bytes=32_000,
            )
            state = runtime.run(
                patient_id=PATIENT_ID,
                task="验证 context retry。",
                case_id="context-retry",
                run_id="context-retry",
                encounter_id="E03",
                as_of=AS_OF["E03"],
            )

            self.assertEqual("complete", state.status)
            self.assertEqual(4, len(state.patient_context.known_facts))
            calls = [
                json.loads(line)
                for line in (
                    root / "runs" / "context-retry" / "tool_calls.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(4, sum(row["tool_name"] == "get_record" for row in calls))
            self.assertEqual(
                2,
                sum(row["tool_name"] == "update_working_context" for row in calls),
            )
            events = [
                json.loads(line)
                for line in (
                    root / "runs" / "context-retry" / "state_events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(
                any(row["event_type"] == "working_context_retry_saved" for row in events)
            )
            self.assertTrue(
                any(
                    row["event_type"] == "working_context_retry_cleared"
                    and row.get("reason") == "corrected_submission_applied"
                    for row in events
                )
            )
class EffectiveMemoryAndVisibilityTests(unittest.TestCase):
    def test_effective_memory_is_patient_scoped_and_resolves_old_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.jsonl")
            old = store.append(
                patient_id="P1",
                source_run_id="run-1",
                kind="unresolved",
                text="旧问题",
                evidence_refs=["rec-1"],
            )
            survivor = store.append(
                patient_id="P1",
                source_run_id="run-1",
                kind="unresolved",
                text="仍活动的问题",
                evidence_refs=["rec-2"],
            )
            store.append(
                patient_id="P2",
                source_run_id="other",
                kind="unresolved",
                text="其他患者",
                evidence_refs=[],
            )
            for index in range(5):
                store.append(
                    patient_id="P1",
                    source_run_id="run-2",
                    kind="prior_work",
                    text=f"较新的工作 {index}",
                    evidence_refs=[],
                )
            store.append(
                patient_id="P1",
                source_run_id="run-2",
                kind="unresolved",
                text="旧问题已解决",
                evidence_refs=["rec-3"],
                status="resolved",
                related_memory_id=old["memory_id"],
            )
            view = store.effective_for_patient("P1", limit=3)
            active_ids = {row["memory_id"] for row in view["active_entries"]}
            self.assertIn(survivor["memory_id"], active_ids)
            self.assertNotIn(old["memory_id"], active_ids)
            self.assertTrue(all(row["patient_id"] == "P1" for row in view["entries"]))
            self.assertGreater(view["omitted_active_count"], 0)

    def test_prompt_visibility_matches_actual_recent_content_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MemoryStore(root / "memory.jsonl")
            runtime = ClinicalAgentRuntime(
                client=object(),
                tools=ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF["E01"])),
                memory=store,
                runs_root=root / "runs",
            )
            state = RunState(run_id="visibility", patient_id=PATIENT_ID, task="test")
            state.step = 3
            state.discovered_record_ids = ["gi-demo-001-rec-001", "gi-demo-001-rec-002"]
            state.discovered_records = [
                {
                    "record_id": record_id,
                    "record_type": "note",
                    "clinical_time": "2025-01-01T00:00:00Z",
                    "available_at": "2025-01-01T00:00:00Z",
                    "title": "测试",
                    "preview": "测试",
                    "status": "最终",
                    "exam_type": None,
                    "workflow_id": None,
                    "workflow_stage": None,
                }
                for record_id in state.discovered_record_ids
            ]
            state.inspected_record_ids = list(state.discovered_record_ids)
            state.record_read_counts = {
                "gi-demo-001-rec-001": 1,
                "gi-demo-001-rec-002": 2,
            }
            state.record_last_read_steps = {
                "gi-demo-001-rec-001": 1,
                "gi-demo-001-rec-002": 2,
            }
            state.patient_context.known_facts = [
                {
                    "entry_id": "persisted",
                    "statement": "已保存。",
                    "evidence_refs": ["gi-demo-001-rec-001"],
                    "clinical_time": None,
                    "temporal_status": "historical",
                }
            ]
            recent = [
                {
                    "call_id": "read-2",
                    "tool_name": "get_record",
                    "information_need": "测试",
                    "result": {
                        "ok": True,
                        "found": True,
                        "record": {"record_id": "gi-demo-001-rec-002", "data": {}},
                    },
                    "raw_result_ref": "tool_calls.jsonl#read-2",
                    "batch_id": "step-2",
                    "batch_position": 1,
                    "batch_size": 1,
                }
            ]
            messages, metadata = runtime._build_prompt(
                state=state,
                task=state.task,
                memory=store.effective_for_patient(PATIENT_ID),
                recent_results=recent,
                recent_activity=[],
                omitted_results=1,
            )
            payload = json.loads(messages[1]["content"])
            visibility = {
                row["record_id"]: row for row in metadata["record_visibility"]
            }
            self.assertTrue(visibility["gi-demo-001-rec-001"]["finding_persisted"])
            self.assertFalse(
                visibility["gi-demo-001-rec-001"]["full_content_currently_visible"]
            )
            self.assertFalse(visibility["gi-demo-001-rec-002"]["finding_persisted"])
            self.assertTrue(
                visibility["gi-demo-001-rec-002"]["full_content_currently_visible"]
            )
            self.assertNotIn("record_visibility", payload)
            self.assertNotIn(
                "full_record_bodies_omitted_from_prompt", payload["context_status"]
            )
            omitted = metadata["prompt_omitted_tool_results"]
            self.assertEqual("recent_window_evicted", omitted[0]["reason"])


class PromptBudgetManagerTests(unittest.TestCase):
    def test_e03_sized_state_fits_without_replaying_viewer_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MemoryStore(root / "memory.jsonl")
            for index in range(4):
                store.append(
                    patient_id=PATIENT_ID,
                    source_run_id="prior-run",
                    kind="unresolved" if index == 0 else "prior_work",
                    text=f"既往 memory {index}：用于指导后续检索。",
                    evidence_refs=["gi-demo-001-rec-013"],
                )

            tools = ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF["E03"]))
            runtime = ClinicalAgentRuntime(
                client=object(),
                tools=tools,
                memory=store,
                runs_root=root / "runs",
                max_steps=10,
                max_tool_calls=12,
                max_prompt_bytes=32_000,
            )
            state = RunState(
                run_id="e03-sized",
                patient_id=PATIENT_ID,
                task="核查后续变化。",
                encounter_id="GI-DEMO-001-E03",
                as_of=AS_OF["E03"],
            )
            state.step = 1
            search_call = ToolCall(
                call_id="search",
                name="search_records",
                arguments={
                    "patient_id": PATIENT_ID,
                    "information_need": "发现可用记录。",
                    "limit": 20,
                },
            )
            search_result = tools.execute(
                search_call.name, search_call.arguments, state
            )
            state.discovered_records = [
                {
                    key: row.get(key)
                    for key in (
                        "record_id",
                        "record_type",
                        "clinical_time",
                        "available_at",
                        "title",
                        "preview",
                        "status",
                        "exam_type",
                        "workflow_id",
                        "workflow_stage",
                    )
                }
                for row in search_result["records"]
            ]
            state.discovered_record_ids = [
                row["record_id"] for row in state.discovered_records
            ]
            activity = [runtime._activity_view(search_call, search_result)]
            activity[0]["step"] = 1

            state.step = 2
            recent = []
            for position, record_id in enumerate(
                (
                    "gi-demo-001-rec-018",
                    "gi-demo-001-rec-017",
                    "gi-demo-001-rec-016",
                    "gi-demo-001-rec-015",
                ),
                1,
            ):
                call = ToolCall(
                    call_id=f"read-{position}",
                    name="get_record",
                    arguments={
                        "patient_id": PATIENT_ID,
                        "information_need": "读取后续记录。",
                        "record_id": record_id,
                    },
                )
                result = tools.execute(call.name, call.arguments, state)
                state.inspected_record_ids.append(record_id)
                state.record_read_counts[record_id] = 1
                state.record_last_read_steps[record_id] = 2
                recent.append(
                    runtime._recent_result_entry(
                        call=call,
                        result=result,
                        batch_id="step-2",
                        batch_position=position,
                        batch_size=4,
                    )
                )
                row = runtime._activity_view(call, result)
                row["step"] = 2
                activity.append(row)

            state.step = 3
            messages, metadata = runtime._build_prompt(
                state=state,
                task=state.task,
                memory=store.effective_for_patient(PATIENT_ID),
                recent_results=recent,
                recent_activity=activity,
                omitted_results=1,
                tool_call_count=10,
            )
            payload = json.loads(messages[1]["content"])

            self.assertLessEqual(metadata["prompt_bytes"], 32_000)
            self.assertEqual(18, metadata["retrieval_catalog"]["included_count"])
            self.assertLessEqual(payload["retrieval_catalog"]["included_count"], 12)
            self.assertNotIn("record_visibility", payload)
            self.assertNotIn("active_entries", payload["patient_memory"])
            self.assertEqual(4, len(payload["patient_memory"]["entries"]))
            self.assertEqual(2, payload["execution_budget"]["remaining_tool_calls"])
            self.assertEqual(
                4, payload["execution_budget"]["max_tool_calls_per_batch"]
            )
            self.assertIn("payload_component_bytes", metadata["prompt_budget"])
            self.assertEqual(18, len(metadata["record_visibility"]))
            self.assertEqual(5, len(metadata["recent_tool_activity"]))


class DeterministicMemoryLifecycleTests(unittest.TestCase):
    @staticmethod
    def _call(call_id: str, record_id: str) -> AgentDecision:
        return AgentDecision(
            content="读取用于验证 memory 的新证据。",
            tool_calls=[
                ToolCall(
                    call_id=call_id,
                    name="get_record",
                    arguments={
                        "patient_id": PATIENT_ID,
                        "information_need": "验证跨 encounter 的未解决问题。",
                        "record_id": record_id,
                    },
                )
            ],
        )

    @staticmethod
    def _final(refs: list[str], updates: list[dict]) -> AgentDecision:
        return AgentDecision(
            content=json.dumps(
                {
                    "final_text": "确定性 memory 生命周期测试结果。",
                    "run_summary": "按阶段读取证据并更新 memory。",
                    "evidence_refs": refs,
                    "memory_updates": updates,
                },
                ensure_ascii=False,
            )
        )

    def test_unresolved_entries_are_loaded_then_resolved_or_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MemoryStore(root / "memory.jsonl")
            runs = root / "runs"

            e01_client = ScriptedClient(
                [
                    self._call("e01-read", "gi-demo-001-rec-005"),
                    self._final(
                        ["gi-demo-001-rec-005"],
                        [
                            {
                                "kind": "unresolved",
                                "text": "等待后续专科检查结果。",
                                "evidence_refs": ["gi-demo-001-rec-005"],
                                "status": "active",
                            },
                            {
                                "kind": "prior_work",
                                "text": "转诊已提出但尚未完成。",
                                "evidence_refs": ["gi-demo-001-rec-005"],
                                "status": "active",
                            },
                        ],
                    ),
                ]
            )
            e01_runtime = ClinicalAgentRuntime(
                client=e01_client,
                tools=ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF["E01"])),
                memory=store,
                runs_root=runs,
            )
            e01_state = e01_runtime.run(
                patient_id=PATIENT_ID,
                task="E01",
                case_id="memory-lifecycle",
                run_id="memory-e01",
                encounter_id="E01",
                as_of=AS_OF["E01"],
            )
            self.assertEqual("complete", e01_state.status)
            initial = store.for_patient(PATIENT_ID)
            unresolved_id = initial[0]["memory_id"]
            prior_work_id = initial[1]["memory_id"]

            def inspect_e02(messages, _tools):
                memory = json.loads(messages[1]["content"])["patient_memory"]
                loaded = {row["memory_id"]: row for row in memory["entries"]}
                self.assertEqual("active", loaded[unresolved_id]["effective_status"])
                self.assertEqual("active", loaded[prior_work_id]["effective_status"])
                return self._call("e02-read", "gi-demo-001-rec-007")

            e02_client = ScriptedClient(
                [
                    inspect_e02,
                    self._final(
                        ["gi-demo-001-rec-007"],
                        [
                            {
                                "kind": "unresolved",
                                "text": "后续检查已提供所需证据。",
                                "evidence_refs": ["gi-demo-001-rec-007"],
                                "status": "resolved",
                                "related_memory_id": unresolved_id,
                            },
                            {
                                "kind": "prior_work",
                                "text": "专科路径已有更新结论。",
                                "evidence_refs": ["gi-demo-001-rec-007"],
                                "status": "superseded",
                                "related_memory_id": prior_work_id,
                            },
                        ],
                    ),
                ]
            )
            e02_runtime = ClinicalAgentRuntime(
                client=e02_client,
                tools=ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF["E02"])),
                memory=store,
                runs_root=runs,
            )
            e02_state = e02_runtime.run(
                patient_id=PATIENT_ID,
                task="E02",
                case_id="memory-lifecycle",
                run_id="memory-e02",
                encounter_id="E02",
                as_of=AS_OF["E02"],
            )
            self.assertEqual("complete", e02_state.status)

            effective = store.effective_for_patient(PATIENT_ID)
            by_id = {row["memory_id"]: row for row in effective["entries"]}
            self.assertEqual("resolved", by_id[unresolved_id]["effective_status"])
            self.assertEqual("superseded", by_id[prior_work_id]["effective_status"])
            self.assertNotIn(
                unresolved_id,
                {row["memory_id"] for row in effective["active_entries"]},
            )

            def inspect_e03(messages, _tools):
                entries = json.loads(messages[1]["content"])["patient_memory"][
                    "entries"
                ]
                current = {row["memory_id"]: row for row in entries}
                self.assertEqual("resolved", current[unresolved_id]["effective_status"])
                self.assertEqual("superseded", current[prior_work_id]["effective_status"])
                return self._final([], [])

            e03_runtime = ClinicalAgentRuntime(
                client=ScriptedClient([inspect_e03]),
                tools=ToolRegistry(MockDatabase(CASE_DIR, as_of=AS_OF["E03"])),
                memory=store,
                runs_root=runs,
            )
            e03_state = e03_runtime.run(
                patient_id=PATIENT_ID,
                task="E03",
                case_id="memory-lifecycle",
                run_id="memory-e03",
                encounter_id="E03",
                as_of=AS_OF["E03"],
            )
            self.assertEqual("complete", e03_state.status)


if __name__ == "__main__":
    unittest.main()
