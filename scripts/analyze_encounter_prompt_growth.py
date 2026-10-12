from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clinical_agent_explorer_phase1.storage import read_jsonl


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs" / "gastro_demo_encounters"
E01 = RUNS / "E01" / "p1-run-e544b90b3855"
E03 = RUNS / "E03" / "p1-run-bf871e9a57f2"

# Captured before the runtime repair while the frozen V4.1 source hash was
# AF0A2FD248335F4BFAA8894EFE5709D19529EE57D6D30B82286D1BA9A4CD1301.
# Step 3 never produced a prompt snapshot because the old builder failed.
FROZEN_E03_STEP3_TRIM_CANDIDATES = [
    {"included_recent_result_count": 4, "removed_recent_result_count": 0, "total_prompt_bytes": 37085},
    {"included_recent_result_count": 3, "removed_recent_result_count": 1, "total_prompt_bytes": 35534},
    {"included_recent_result_count": 2, "removed_recent_result_count": 2, "total_prompt_bytes": 34287},
    {"included_recent_result_count": 1, "removed_recent_result_count": 3, "total_prompt_bytes": 33263},
    {
        "included_recent_result_count": 0,
        "removed_recent_result_count": 4,
        "total_prompt_bytes": 32137,
        "payload_components": {
            "patient_memory": 5114,
            "retrieval_catalog": 7571,
            "record_visibility": 3782,
            "context_status": 1443,
            "recent_tool_activity": 783,
            "recent_tool_results": 2,
        },
    },
]


def encoded_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def prompt_components(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(snapshot["messages"][1]["content"])
    return {
        "step": snapshot["step"],
        "total_prompt_bytes": encoded_bytes(
            {"messages": snapshot["messages"], "tools": snapshot["tools"]}
        ),
        "system_message_bytes": encoded_bytes(snapshot["messages"][0]),
        "tool_schema_bytes": encoded_bytes(snapshot["tools"]),
        "user_payload_bytes": encoded_bytes(payload),
        "payload_components": {
            key: encoded_bytes(value) for key, value in payload.items()
        },
    }


def main() -> None:
    e03_snapshots = read_jsonl(E03 / "prompt_snapshots.jsonl")
    e03 = [prompt_components(row) for row in e03_snapshots]

    e01_snapshots = read_jsonl(E01 / "prompt_snapshots.jsonl")
    e01_steps = []
    for snapshot in e01_snapshots:
        if snapshot["step"] < 3:
            continue
        payload = json.loads(snapshot["messages"][1]["content"])
        e01_steps.append(
            {
                "step": snapshot["step"],
                "prompt_bytes": snapshot["prompt_bytes"],
                "has_execution_budget": "execution_budget" in payload,
                "inspected_record_ids": payload["retrieval_state"][
                    "inspected_record_ids"
                ],
                "visible_record_ids": payload["context_status"][
                    "fresh_full_record_ids_in_recent_results"
                ],
                "persisted_record_ids": [
                    row["record_id"]
                    for row in payload["record_visibility"]
                    if row["finding_persisted"]
                ],
                "omitted_body_count": len(
                    payload["context_status"][
                        "full_record_bodies_omitted_from_prompt"
                    ]
                ),
            }
        )

    model_calls = {
        row["step"]: {
            "content": row.get("content"),
            "requested_tool_names": [call["name"] for call in row["tool_calls"]],
            "requested_tool_count": len(row["tool_calls"]),
        }
        for row in read_jsonl(E01 / "model_calls.jsonl")
        if row["step"] >= 3
    }
    events = read_jsonl(E01 / "state_events.jsonl")
    rejection = next(
        row
        for row in events
        if row.get("event_type") == "tool_batch_rejected"
        and row.get("reason") == "tool_call_limit"
    )

    print(
        json.dumps(
            {
                "e03_prompt_growth": e03,
                "e03_failed_step_trim_candidates": FROZEN_E03_STEP3_TRIM_CANDIDATES,
                "e01_steps_3_to_5": e01_steps,
                "e01_model_requests": model_calls,
                "e01_final_rejection": {
                    "remaining_tool_call_budget": rejection[
                        "remaining_tool_call_budget"
                    ],
                    "returned_tool_call_count": rejection[
                        "returned_tool_call_count"
                    ],
                    "executed": rejection["executed"],
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
