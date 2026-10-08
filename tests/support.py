from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from clinical_agent_explorer.model_client import AgentClient
from clinical_agent_explorer.models import AgentDecision
from clinical_agent_explorer.runtime import ClinicalAgentRuntime
from clinical_agent_explorer.storage import MemoryStore, MockDatabase
from clinical_agent_explorer.tools import ToolRegistry


DecisionFactory = Callable[[list[dict[str, Any]], list[dict[str, Any]]], AgentDecision]


class ScriptedClient(AgentClient):
    def __init__(self, decisions: list[AgentDecision | DecisionFactory]) -> None:
        self.decisions = list(decisions)
        self.contexts: list[list[dict[str, Any]]] = []
        self.tool_sets: list[list[dict[str, Any]]] = []

    def decide(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentDecision:
        self.contexts.append(messages)
        self.tool_sets.append(tools)
        if not self.decisions:
            raise AssertionError("scripted client ran out of decisions")
        next_decision = self.decisions.pop(0)
        if callable(next_decision):
            return next_decision(messages, tools)
        return next_decision


class RepeatingClient(AgentClient):
    def __init__(self, decision: AgentDecision) -> None:
        self.decision = decision

    def decide(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentDecision:
        return self.decision


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    database = root / "mock_db"
    database.mkdir(parents=True)
    patients = [
        {
            "record_id": "patient-P-001",
            "patient_id": "P-001",
            "source": "test_fixture",
            "demographics": {"birth_year": 1980},
        }
    ]
    records = [
        {
            "record_id": "rec-old",
            "patient_id": "P-001",
            "record_type": "note",
            "clinical_time": "2024-01-01T00:00:00Z",
            "source": "test_fixture",
            "data": {"value": "older"},
        },
        {
            "record_id": "rec-new",
            "patient_id": "P-001",
            "record_type": "note",
            "clinical_time": "2025-01-01T00:00:00Z",
            "source": "test_fixture",
            "data": {"value": "newer"},
        },
    ]
    for filename, rows in (
        ("patients.jsonl", patients),
        ("clinical_records.jsonl", records),
        ("orders.jsonl", []),
    ):
        text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        (database / filename).write_text(text, encoding="utf-8")
    memory = root / "memory" / "patient_memory.jsonl"
    memory.parent.mkdir(parents=True)
    memory.touch()
    runs = root / "runs"
    return database, memory, runs


def make_runtime(
    root: Path,
    client: AgentClient,
    *,
    max_steps: int = 8,
) -> tuple[ClinicalAgentRuntime, Path, Path]:
    database, memory, runs = write_fixture(root)
    runtime = ClinicalAgentRuntime(
        client=client,
        tools=ToolRegistry(MockDatabase(database)),
        memory=MemoryStore(memory),
        runs_root=runs,
        max_steps=max_steps,
    )
    return runtime, memory, runs


def final_decision(
    *,
    evidence_refs: list[str] | None = None,
    memory_updates: list[dict[str, Any]] | None = None,
) -> AgentDecision:
    return AgentDecision(
        content=json.dumps(
            {
                "final_text": "Synthetic run complete.",
                "working_summary": "Observed only the scripted test evidence.",
                "unresolved_questions": [],
                "evidence_refs": evidence_refs or [],
                "memory_updates": memory_updates or [],
            }
        )
    )

