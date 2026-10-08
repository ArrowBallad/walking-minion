from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PatientContext:
    known_facts: list[dict[str, Any]] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    relevant_record_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunState:
    run_id: str
    patient_id: str
    task: str
    status: str = "running"
    step: int = 0
    discovered_record_ids: list[str] = field(default_factory=list)
    discovered_records: list[dict[str, Any]] = field(default_factory=list)
    inspected_record_ids: list[str] = field(default_factory=list)
    patient_record_ids: list[str] = field(default_factory=list)
    patient_context: PatientContext = field(default_factory=PatientContext)
    proposed_actions: list[dict[str, Any]] = field(default_factory=list)
    verified_actions: list[dict[str, Any]] = field(default_factory=list)
    context_warnings: list[dict[str, Any]] = field(default_factory=list)
    final_summary: str = ""
    final_evidence_refs: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentDecision:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    response_metadata: dict[str, Any] = field(default_factory=dict)
