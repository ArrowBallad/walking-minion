from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "runs" / "gastro_demo_multi_patient"
WORK_ROOT = ROOT / "work" / "gastro_demo_multi_patient"
CASE_ROOT = ROOT / "cases" / "gastro_demo"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sole_run_dir(patient_id: str, encounter_label: str) -> Path:
    parent = RUN_ROOT / patient_id / encounter_label
    candidates = [path for path in parent.iterdir() if path.is_dir()]
    assert len(candidates) == 1, f"{parent}: expected one run, got {len(candidates)}"
    return candidates[0]


def prompt_reference_leaks(prompt_rows: list[dict[str, Any]]) -> list[str]:
    serialized = json.dumps(prompt_rows, ensure_ascii=False)
    markers = (
        "Underlying case truth",
        "Acceptable Agent conclusions",
        "gastro_demo_reference",
        "_reference.md",
        "_sources.md",
    )
    return [marker for marker in markers if marker in serialized]


def analyze_run(patient_index: int, encounter_index: int) -> dict[str, Any]:
    patient_id = f"GI-DEMO-{patient_index:03d}"
    encounter_label = f"E{encounter_index:02d}"
    encounter_id = f"{patient_id}-{encounter_label}"
    case_id = f"gi_demo_{patient_index:03d}"
    run_dir = sole_run_dir(patient_id, encounter_label)
    state = load_json(run_dir / "run_state.json")
    metadata = load_json(run_dir / "metadata.json")
    model_calls = load_jsonl(run_dir / "model_calls.jsonl")
    tool_calls = load_jsonl(run_dir / "tool_calls.jsonl")
    events = load_jsonl(run_dir / "state_events.jsonl")
    prompts = load_jsonl(run_dir / "prompt_snapshots.jsonl")
    encounter_doc = load_json(CASE_ROOT / case_id / "encounters.json")
    encounter = next(
        item for item in encounter_doc["encounters"] if item["encounter_id"] == encounter_id
    )
    record_metadata = load_json(CASE_ROOT / case_id / "record_metadata.json")

    requested_calls = sum(len(row.get("tool_calls") or []) for row in model_calls)
    executed_calls = len(tool_calls)
    tool_error_calls = [
        row
        for row in tool_calls
        if isinstance(row.get("output"), dict) and row["output"].get("ok") is False
    ]
    unexecuted_rejections = max(0, requested_calls - executed_calls)
    rejected_calls = unexecuted_rejections + len(tool_error_calls)
    reads = [row for row in tool_calls if row.get("tool_name") == "get_record"]
    read_counts = Counter(str(row["input"].get("record_id")) for row in reads)
    duplicate_reads = sum(count - 1 for count in read_counts.values() if count > 1)
    context_calls = [
        row for row in tool_calls if row.get("tool_name") == "update_working_context"
    ]
    successful_context_calls = [
        row for row in context_calls if (row.get("output") or {}).get("ok") is True
    ]
    failed_context_calls = [
        row for row in context_calls if (row.get("output") or {}).get("ok") is False
    ]
    final_events = [row for row in events if row.get("event_type") == "final"]
    memory_written = sum(int(row.get("memory_updates_written", 0)) for row in final_events)
    memory_rejected = sum(int(row.get("memory_updates_rejected", 0)) for row in final_events)
    memory_path = (
        WORK_ROOT
        / f"gi-demo-{patient_index:03d}-multi-v1"
        / "memory"
        / "patient_memory.jsonl"
    )
    patient_memory = load_jsonl(memory_path)
    source_memory = [row for row in patient_memory if row.get("source_run_id") == state["run_id"]]
    memory_transitions = sum(
        1
        for row in source_memory
        if row.get("status") in {"resolved", "superseded"}
        and isinstance(row.get("related_memory_id"), str)
    )
    prompt_bytes = [int(row.get("prompt_bytes", 0)) for row in prompts]
    trim_count = sum(len((row.get("prompt_budget") or {}).get("trims") or []) for row in prompts)
    usage = Counter()
    provider_models: set[str] = set()
    for row in model_calls:
        response_metadata = row.get("response_metadata") or {}
        if response_metadata.get("model"):
            provider_models.add(str(response_metadata["model"]))
        for key, value in (response_metadata.get("usage") or {}).items():
            if isinstance(value, int):
                usage[key] += value

    as_of = str(encounter["as_of"])
    future_inspected = [
        record_id
        for record_id in state.get("inspected_record_ids", [])
        if record_metadata[record_id]["available_at"] > as_of
    ]
    error_codes = [
        str((row.get("output") or {}).get("error", {}).get("code"))
        for row in tool_error_calls
        if isinstance((row.get("output") or {}).get("error"), dict)
    ]
    rejected_batches = [row for row in events if row.get("event_type") == "tool_batch_rejected"]
    final_rejections = [row for row in events if row.get("event_type") == "final_rejected"]
    retry_events = [
        row for row in events if str(row.get("event_type", "")).startswith("working_context_retry_")
    ]

    return {
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "as_of": as_of,
        "expected_available_record_count": encounter["expected_available_record_count"],
        "run_id": state["run_id"],
        "run_path": str(run_dir.relative_to(ROOT)).replace("\\", "/"),
        "runtime_version": metadata["runtime_version"],
        "requested_model": metadata["model"]["model"],
        "provider_models": sorted(provider_models),
        "status": state["status"],
        "stop_reason": state["stop_reason"],
        "model_calls": len(model_calls),
        "tool_calls_requested": requested_calls,
        "tool_calls_executed": executed_calls,
        "tool_calls_rejected": rejected_calls,
        "unexecuted_tool_calls": unexecuted_rejections,
        "executed_tool_errors": len(tool_error_calls),
        "error_codes": error_codes,
        "rejected_batch_count": len(rejected_batches),
        "final_rejection_count": len(final_rejections),
        "unique_full_record_reads": len(read_counts),
        "duplicate_full_record_reads": duplicate_reads,
        "full_record_read_ids": list(read_counts),
        "working_context_calls": len(context_calls),
        "working_context_successes": len(successful_context_calls),
        "working_context_failures": len(failed_context_calls),
        "working_context_final_findings": len(
            (state.get("patient_context") or {}).get("known_facts") or []
        ),
        "working_context_final_questions": len(
            (state.get("patient_context") or {}).get("question_states") or []
        ),
        "working_context_retry_events": len(retry_events),
        "memory_loaded": len(state.get("loaded_memory_ids") or []),
        "memory_written": memory_written,
        "memory_rejected": memory_rejected,
        "memory_transitions": memory_transitions,
        "max_prompt_bytes": max(prompt_bytes, default=0),
        "prompt_trim_count": trim_count,
        "agent_final_exists": bool(final_events) and bool(state.get("final_summary")),
        "final_artifact_exists": (run_dir / "final.md").is_file(),
        "final_evidence_refs": state.get("final_evidence_refs") or [],
        "prompt_reference_leaks": prompt_reference_leaks(prompts),
        "future_inspected_record_ids": future_inspected,
        "token_usage": dict(usage),
    }


def analyze_all() -> dict[str, Any]:
    runs = [
        analyze_run(patient_index, encounter_index)
        for patient_index in range(1, 6)
        for encounter_index in range(1, 4)
    ]
    run_ids = [row["run_id"] for row in runs]
    assert len(run_ids) == len(set(run_ids)) == 15
    assert not any(row["prompt_reference_leaks"] for row in runs)
    assert not any(row["future_inspected_record_ids"] for row in runs)
    assert all(row["max_prompt_bytes"] <= 32000 for row in runs)
    return {
        "run_count": len(runs),
        "complete_count": sum(row["status"] == "complete" for row in runs),
        "stopped_count": sum(row["status"] != "complete" for row in runs),
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = analyze_all()
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        (RUN_ROOT / "run_metrics.json").write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
