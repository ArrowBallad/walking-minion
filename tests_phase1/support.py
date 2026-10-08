from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from clinical_agent_explorer_phase1.model_client import AgentClient
from clinical_agent_explorer_phase1.models import AgentDecision
from clinical_agent_explorer_phase1.runtime import ClinicalAgentRuntime
from clinical_agent_explorer_phase1.storage import MemoryStore, MockDatabase
from clinical_agent_explorer_phase1.tools import ToolRegistry


DecisionFactory = Callable[[list[dict[str, Any]], list[dict[str, Any]]], AgentDecision]


class ScriptedClient(AgentClient):
    def __init__(self, decisions: list[AgentDecision | DecisionFactory]) -> None:
        self.decisions = list(decisions)
        self.contexts: list[list[dict[str, Any]]] = []

    def decide(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentDecision:
        self.contexts.append(messages)
        if not self.decisions:
            raise AssertionError("scripted client ran out of decisions")
        decision = self.decisions.pop(0)
        return decision(messages, tools) if callable(decision) else decision


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    database = root / "mock_db"
    database.mkdir(parents=True)
    patients = [
        {
            "record_id": "patient-P1",
            "patient_id": "P1",
            "demographics": {"birth_year": 1980, "sex": "女"},
            "source": "test_fixture",
        }
    ]
    records = [
        {
            "record_id": f"rec-{index}",
            "patient_id": "P1",
            "record_type": "note" if index % 2 else "lab",
            "clinical_time": f"2026-01-{index:02d}T00:00:00Z",
            "title": f"索引标题 {index}",
            "preview": f"短预览 {index}",
            "status": "最终",
            "keywords": ["目标"] if index in {3, 5} else ["常规"],
            "source": "test_fixture",
            "data": {"full_text": f"仅全文可见的内容 {index}", "value": index},
        }
        for index in range(1, 7)
    ]
    for filename, rows in (
        ("patients.jsonl", patients),
        ("clinical_records.jsonl", records),
        ("orders.jsonl", []),
    ):
        (database / filename).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    memory = root / "memory" / "patient_memory.jsonl"
    memory.parent.mkdir(parents=True)
    memory.touch()
    return database, memory, root / "runs"


def make_runtime(
    root: Path,
    client: AgentClient,
    *,
    max_steps: int = 10,
    max_tool_calls: int = 20,
    recent_result_limit: int = 2,
) -> tuple[ClinicalAgentRuntime, Path, Path]:
    database, memory, runs = write_fixture(root)
    runtime = ClinicalAgentRuntime(
        client=client,
        tools=ToolRegistry(MockDatabase(database)),
        memory=MemoryStore(memory),
        runs_root=runs,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        recent_result_limit=recent_result_limit,
    )
    return runtime, memory, runs

