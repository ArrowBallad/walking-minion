from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "runs" / "gastro_demo_multi_patient"
CASE_ROOT = ROOT / "cases" / "gastro_demo"
REFERENCE_ROOT = ROOT / "docs" / "gastro_demo_reference"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> None:
    freeze = load_json(RUN_ROOT / "execution_freeze.json")
    metrics = load_json(RUN_ROOT / "run_metrics.json")
    case_hashes: dict[str, dict[str, str]] = {}
    for index in range(1, 6):
        case_id = f"gi_demo_{index:03d}"
        files = {
            "patients.jsonl": CASE_ROOT / case_id / "patients.jsonl",
            "clinical_records.jsonl": CASE_ROOT / case_id / "clinical_records.jsonl",
            "task.md": CASE_ROOT / case_id / "task.md",
            "encounters.json": CASE_ROOT / case_id / "encounters.json",
            "record_metadata.json": CASE_ROOT / case_id / "record_metadata.json",
            "evaluator_reference.md": REFERENCE_ROOT / f"{case_id}_reference.md",
        }
        case_hashes[case_id] = {name: sha256(path) for name, path in files.items()}

    runs: list[dict[str, Any]] = []
    completed_at = ""
    for row in metrics["runs"]:
        run_path = ROOT / row["run_path"]
        state = load_json(run_path / "run_state.json")
        completed_at = max(completed_at, str(state["updated_at"]))
        artifact_hashes = {
            name: sha256(run_path / name)
            for name in (
                "metadata.json",
                "run_state.json",
                "prompt_snapshots.jsonl",
                "model_calls.jsonl",
                "tool_calls.jsonl",
                "state_events.jsonl",
                "final.md",
            )
        }
        runs.append(
            {
                "patient_id": row["patient_id"],
                "encounter_id": row["encounter_id"],
                "as_of": row["as_of"],
                "available_record_count": row["expected_available_record_count"],
                "run_id": row["run_id"],
                "run_path": row["run_path"],
                "status": row["status"],
                "stop_reason": row["stop_reason"],
                "model_calls": row["model_calls"],
                "tool_calls": {
                    "requested": row["tool_calls_requested"],
                    "executed": row["tool_calls_executed"],
                    "rejected": row["tool_calls_rejected"],
                },
                "unique_full_record_reads": row["unique_full_record_reads"],
                "duplicate_full_record_reads": row["duplicate_full_record_reads"],
                "working_context": {
                    "calls": row["working_context_calls"],
                    "successes": row["working_context_successes"],
                    "failures": row["working_context_failures"],
                    "final_findings": row["working_context_final_findings"],
                },
                "memory": {
                    "loaded": row["memory_loaded"],
                    "written": row["memory_written"],
                    "rejected": row["memory_rejected"],
                    "transitions": row["memory_transitions"],
                },
                "max_prompt_bytes": row["max_prompt_bytes"],
                "agent_final_exists": row["agent_final_exists"],
                "token_usage": row["token_usage"],
                "artifact_sha256": artifact_hashes,
            }
        )

    manifest = {
        "experiment_id": "gastro-demo-five-patient-three-encounter-v1",
        "completed_at": completed_at,
        "git_commit": freeze["git_commit"],
        "worktree_clean_at_freeze": freeze["worktree_clean"],
        "execution_freeze": "runs/gastro_demo_multi_patient/execution_freeze.json",
        "runtime_version": freeze["runtime_version"],
        "model": {
            **freeze["model_configuration"],
            "provider_response_model": "deepseek-flash",
        },
        "limits_per_encounter": freeze["limits_per_encounter"],
        "pre_run_validation": freeze["pre_run_validation"],
        "design": {
            "patient_count": 5,
            "encounters_per_patient": 3,
            "planned_run_count": 15,
            "actual_run_count": metrics["run_count"],
            "complete_count": metrics["complete_count"],
            "stopped_count": metrics["stopped_count"],
        },
        "audits": {
            "future_record_reads": 0,
            "evaluator_reference_prompt_leaks": 0,
            "cross_patient_memory_leaks": 0,
            "prompt_limit_violations": 0,
        },
        "runtime_sha256": freeze["runtime_sha256"],
        "case_sha256": case_hashes,
        "run_metrics": "runs/gastro_demo_multi_patient/run_metrics.json",
        "evaluation_report": "docs/gastro_demo_review/gi_demo_multi_patient_evaluation.md",
        "deterministic_validator": "scripts/validate_gastro_multi_patient_encounters.py",
        "runs": runs,
        "preservation_note": freeze["preservation_note"],
    }
    (RUN_ROOT / "experiment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {RUN_ROOT / 'experiment_manifest.json'} with {len(runs)} runs.")


if __name__ == "__main__":
    main()
