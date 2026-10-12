from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from clinical_agent_explorer_phase1.storage import read_jsonl


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "runs" / "gastro_demo_encounters_v2"
MEMORY = (
    ROOT
    / "work"
    / "gastro_demo"
    / "gi-demo-001-encounters-v2"
    / "memory"
    / "patient_memory.jsonl"
)
RUNS = {
    "E01": "p1-run-2ed3c307f21d",
    "E02": "p1-run-7b4ee7428483",
    "E03": "p1-run-94272b0cdf88",
}
AVAILABLE_MAX = {"E01": 5, "E02": 13, "E03": 18}


def summarize(encounter: str, run_id: str, memory_rows: list[dict[str, Any]]) -> dict:
    run = RUN_ROOT / encounter / run_id
    state = json.loads((run / "run_state.json").read_text(encoding="utf-8"))
    prompts = read_jsonl(run / "prompt_snapshots.jsonl")
    model_calls = read_jsonl(run / "model_calls.jsonl")
    tool_calls = read_jsonl(run / "tool_calls.jsonl")
    events = read_jsonl(run / "state_events.jsonl")
    get_records = [
        row["input"]["record_id"]
        for row in tool_calls
        if row["tool_name"] == "get_record"
    ]
    counts = Counter(get_records)
    loaded = next(row for row in events if row["event_type"] == "run_started")
    prompt_payloads = [json.loads(row["messages"][1]["content"]) for row in prompts]
    all_prompt_text = json.dumps(
        [row["messages"] for row in prompts], ensure_ascii=False
    )
    forbidden_ids = [
        f"gi-demo-001-rec-{index:03d}"
        for index in range(AVAILABLE_MAX[encounter] + 1, 19)
    ]
    usage = [row.get("response_metadata", {}).get("usage") or {} for row in model_calls]
    return {
        "run_id": run_id,
        "status": state["status"],
        "stop_reason": state["stop_reason"],
        "model_calls": len(model_calls),
        "executed_tool_calls": len(tool_calls),
        "tool_counts": dict(Counter(row["tool_name"] for row in tool_calls)),
        "record_reads": get_records,
        "unique_record_reads": list(dict.fromkeys(get_records)),
        "duplicate_record_reads": {
            record_id: count - 1 for record_id, count in counts.items() if count > 1
        },
        "prompt_bytes": [row["prompt_bytes"] for row in prompts],
        "max_prompt_bytes": max(row["prompt_bytes"] for row in prompts),
        "prompt_trim_counts": [
            len(row.get("prompt_budget", {}).get("trims", [])) for row in prompts
        ],
        "remaining_tool_call_budget_by_step": [
            payload["execution_budget"]["remaining_tool_calls"]
            for payload in prompt_payloads
        ],
        "loaded_memory_ids": loaded.get("loaded_memory_ids", []),
        "active_memory_ids": loaded.get("active_memory_ids", []),
        "memory_updates": [
            {
                "memory_id": row["memory_id"],
                "kind": row["kind"],
                "status": row["status"],
                "related_memory_id": row.get("related_memory_id"),
            }
            for row in memory_rows
            if row["source_run_id"] == run_id
        ],
        "working_context_finding_count": len(
            state["patient_context"]["known_facts"]
        ),
        "working_context_question_count": len(
            state["patient_context"]["question_states"]
        ),
        "future_record_ids_in_model_prompts": [
            record_id for record_id in forbidden_ids if record_id in all_prompt_text
        ],
        "token_usage": {
            "prompt_tokens": sum(row.get("prompt_tokens", 0) for row in usage),
            "completion_tokens": sum(
                row.get("completion_tokens", 0) for row in usage
            ),
            "total_tokens": sum(row.get("total_tokens", 0) for row in usage),
        },
        "provider_models": sorted(
            {
                str(row.get("response_metadata", {}).get("model"))
                for row in model_calls
            }
        ),
    }


def main() -> None:
    memory_rows = read_jsonl(MEMORY)
    report = {
        encounter: summarize(encounter, run_id, memory_rows)
        for encounter, run_id in RUNS.items()
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
